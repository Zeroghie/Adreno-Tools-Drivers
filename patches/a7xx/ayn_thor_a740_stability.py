#!/usr/bin/env python3
"""
AYN Thor (Adreno 740 / a7xx_gen2) stability patch.

1. tu_pipeline.cc: process-wide limiter on concurrent vkCreate{Graphics,Compute}Pipelines
   calls. The limit is derived at first use from MemAvailable (/proc/meminfo) and can be
   overridden with TU_MAX_PARALLEL_PIPELINE_COMPILES. Prevents std::bad_alloc when an
   emulator compiles many pipelines on many threads with 12 GB of shared RAM.
2. tu_device.cc: lowers the advertised system heap (3/4 -> 5/8 of RAM) so the app does
   not budget more device memory than the emulator can actually afford.
3. Does NOT touch any extension/feature list (verified by before/after counts).
   Reports (read-only) any Gen8/a8xx-gated code so it can be reviewed against A740.

Idempotent. Run from the Mesa source root.
"""
import re
import sys

DEVICE_CC = "src/freedreno/vulkan/tu_device.cc"
PIPELINE_CC = "src/freedreno/vulkan/tu_pipeline.cc"
MARKER = "AYN-A740-STABILITY"

LIMITER = r'''
/* AYN-A740-STABILITY BEGIN: cap concurrent pipeline compilations */
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <unistd.h>

namespace {
class ayn_compile_limiter {
 public:
   static ayn_compile_limiter &get()
   {
      static ayn_compile_limiter instance;
      return instance;
   }

   void acquire()
   {
      std::unique_lock<std::mutex> lock(mtx);
      cv.wait(lock, [this] { return active < limit; });
      active++;
   }

   void release()
   {
      {
         std::lock_guard<std::mutex> lock(mtx);
         active--;
      }
      cv.notify_one();
   }

 private:
   ayn_compile_limiter()
   {
      const char *env = getenv("TU_MAX_PARALLEL_PIPELINE_COMPILES");
      if (env && atoi(env) > 0) {
         limit = atoi(env);
         return;
      }

      long avail_mb = 0;
      FILE *f = fopen("/proc/meminfo", "r");
      if (f) {
         char line[128];
         while (fgets(line, sizeof(line), f)) {
            unsigned long kb;
            if (sscanf(line, "MemAvailable: %lu kB", &kb) == 1) {
               avail_mb = (long)(kb / 1024);
               break;
            }
         }
         fclose(f);
      }

      /* Keep ~2 GiB for the emulator, budget ~768 MiB per concurrent compile. */
      long slots = avail_mb > 0 ? (avail_mb - 2048) / 768 : 2;
      limit = slots < 1 ? 1 : (slots > 4 ? 4 : (int)slots);
   }

   std::mutex mtx;
   std::condition_variable cv;
   int active = 0;
   int limit = 2;
};

struct ayn_compile_guard {
   ayn_compile_guard() { ayn_compile_limiter::get().acquire(); }
   ~ayn_compile_guard() { ayn_compile_limiter::get().release(); }
   ayn_compile_guard(const ayn_compile_guard &) = delete;
   ayn_compile_guard &operator=(const ayn_compile_guard &) = delete;
};
} /* namespace */
/* AYN-A740-STABILITY END */
'''

GUARD_LINE = "   ayn_compile_guard ayn_guard_; /* AYN-A740-STABILITY */\n"
ENTRYPOINTS = ("tu_CreateGraphicsPipelines", "tu_CreateComputePipelines")

AUDIT_RE = re.compile(r"(chip\s*[>=]=?\s*8|A8XX|a8xx|gen8|GEN8|a7xx_gen3)")
EXT_RE = re.compile(r"\b(VK_(?:KHR|EXT|VALVE|QCOM|GOOGLE|MESA)_\w+|\.(?:KHR|EXT|VALVE|QCOM)_\w+)")


def read(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError as e:
        print(f"  WARNING: cannot read {path}: {e}", file=sys.stderr)
        return None


def write(path, content):
    with open(path, "w") as f:
        f.write(content)


def brace_balance(text):
    return text.count("{") - text.count("}")


def audit(path, content):
    hits = [(i + 1, l.strip()) for i, l in enumerate(content.splitlines()) if AUDIT_RE.search(l)]
    if hits:
        print(f"  AUDIT {path}: Gen8/a8xx-gated lines to review against A740 (not modified):")
        for no, line in hits[:40]:
            print(f"    {path}:{no}: {line[:110]}")
    else:
        print(f"  AUDIT {path}: no Gen8/a8xx-gated lines found")


# --------------------------------------------------------------------------- tu_pipeline.cc
def patch_pipeline():
    content = read(PIPELINE_CC)
    if content is None:
        return
    audit(PIPELINE_CC, content)

    if MARKER in content and "ayn_compile_guard ayn_guard_" in content:
        # Check that every entrypoint is guarded (handles partial earlier runs).
        pass

    new = content
    before_balance = brace_balance(content)

    if "AYN-A740-STABILITY BEGIN" not in new:
        # Last #include located before the first real definition.
        first_def = re.search(r"^(static|struct|namespace|VKAPI_ATTR|template|enum|class)\b", new, re.M)
        limit_pos = first_def.start() if first_def else len(new)
        includes = [m for m in re.finditer(r"^#include\s+.*\n", new, re.M) if m.end() <= limit_pos]
        if not includes:
            print(f"  WARNING: {PIPELINE_CC}: no include block found, skipping", file=sys.stderr)
            return
        pos = includes[-1].end()
        new = new[:pos] + LIMITER + new[pos:]

    guarded = 0
    for name in ENTRYPOINTS:
        # Definition only: signature up to '{' with no ';' in between (excludes declarations).
        m = re.search(rf"\b{name}\s*\([^{{;]*?\)\s*\{{\n", new)
        if not m:
            print(f"  WARNING: {PIPELINE_CC}: definition of {name} not found, skipping it", file=sys.stderr)
            continue
        # Already guarded? look at the next ~300 chars of the body.
        if "ayn_compile_guard ayn_guard_" in new[m.end():m.end() + 300]:
            guarded += 1
            continue
        new = new[:m.end()] + GUARD_LINE + new[m.end():]
        guarded += 1

    if new == content:
        print(f"  {PIPELINE_CC}: already patched")
        return

    # Sanity: inserted code is brace-balanced; the file must stay as balanced as before.
    if brace_balance(new) != before_balance:
        print(f"  FATAL: brace balance changed in {PIPELINE_CC}, not writing", file=sys.stderr)
        sys.exit(1)
    if guarded == 0:
        print(f"  WARNING: {PIPELINE_CC}: limiter inserted but no entrypoint guarded; not writing", file=sys.stderr)
        return

    write(PIPELINE_CC, new)
    print(f"  {PIPELINE_CC}: pipeline compile limiter added ({guarded}/{len(ENTRYPOINTS)} entrypoints guarded)")


# --------------------------------------------------------------------------- tu_device.cc
def patch_device():
    content = read(DEVICE_CC)
    if content is None:
        return
    audit(DEVICE_CC, content)

    # Same-length copy with comments and string/char literals blanked out, so that
    # regexes and brace counting never see them. Offsets match `content` exactly.
    lit = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", re.S)
    masked = lit.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), content)

    # 1. Locate the definition (not a call) and extract its body by brace matching.
    fn = None
    for m in re.finditer(r"\btu_get_system_heap_size\s*\([^;{]*\)\s*\{", masked):
        line_start = masked.rfind("\n", 0, m.start()) + 1
        if re.search(r"[(=]|\breturn\b", masked[line_start:m.start()]):
            continue  # call context, not a definition
        fn = m
        break
    if not fn:
        print(f"  WARNING: {DEVICE_CC}: definition of tu_get_system_heap_size not found, "
              f"skipping heap cap", file=sys.stderr)
        return

    depth, i = 1, fn.end()
    while i < len(masked) and depth:
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
        i += 1
    if depth:
        print(f"  WARNING: {DEVICE_CC}: unbalanced braces in tu_get_system_heap_size, "
              f"skipping heap cap", file=sys.stderr)
        return

    body_start, body_end = fn.end(), i          # body_end is just past the closing '}'
    body = content[body_start:body_end]
    mbody = masked[body_start:body_end]

    # 2. Always print the full function, with file line numbers.
    first_line = content.count("\n", 0, fn.start()) + 1
    func_text = content[fn.start():body_end]
    print(f"  {DEVICE_CC}: tu_get_system_heap_size as found (from line {first_line}):", flush=True)
    for k, ln in enumerate(func_text.split("\n")):
        print(f"    {first_line + k:5d} | {ln}", flush=True)

    # Idempotency (also covers the marker written by the earlier 5/8 version).
    if f"{MARKER}: heap" in body:
        print(f"  {DEVICE_CC}: heap cap already applied")
        return

    # 3. Find the last `return <EXPR>;` and require it to be at function top level.
    returns = list(re.finditer(r"\breturn\b(?P<e>[^;]*);", mbody))
    if not returns:
        print(f"  WARNING: {DEVICE_CC}: no 'return <expr>;' in tu_get_system_heap_size "
              f"(value may be returned via an out-param), skipping heap cap", file=sys.stderr)
        return
    last = returns[-1]
    expr = body[last.start("e"):last.end("e")].strip()
    if not expr:
        print(f"  WARNING: {DEVICE_CC}: last return in tu_get_system_heap_size has no "
              f"expression, skipping heap cap", file=sys.stderr)
        return
    if "//" in expr:
        print(f"  WARNING: {DEVICE_CC}: line comment inside the return expression, "
              f"skipping heap cap to avoid breaking the parentheses", file=sys.stderr)
        return
    nest = mbody[:last.start()].count("{") - mbody[:last.start()].count("}")
    if nest != 0:
        print(f"  WARNING: {DEVICE_CC}: last return is inside a nested block (depth {nest}), "
              f"there is no final top-level return; skipping heap cap", file=sys.stderr)
        return

    ext_before = sorted(EXT_RE.findall(content))
    bal_before = brace_balance(content)

    new_ret = f"return ({expr}) * 5 / 6; /* {MARKER}: heap cap */"
    new_body = body[:last.start()] + new_ret + body[last.end():]
    new = content[:body_start] + new_body + content[body_end:]

    # 4. Guarantees: no extension/feature token added or removed, braces unchanged.
    if sorted(EXT_RE.findall(new)) != ext_before:
        print(f"  FATAL: extension tokens changed in {DEVICE_CC}, not writing", file=sys.stderr)
        sys.exit(1)
    if brace_balance(new) != bal_before:
        print(f"  FATAL: brace balance changed in {DEVICE_CC}, not writing", file=sys.stderr)
        sys.exit(1)
    if new.count(f"{MARKER}: heap cap") != 1:
        print(f"  FATAL: heap cap marker count != 1 in {DEVICE_CC}, not writing", file=sys.stderr)
        sys.exit(1)

    write(DEVICE_CC, new)
    one_line = " ".join(expr.split())
    print(f"  {DEVICE_CC}: patched last return in tu_get_system_heap_size: "
          f"'return {one_line};' -> 'return ({one_line}) * 5 / 6;' "
          f"({len(returns) - 1} earlier return(s) untouched; "
          f"extensions untouched: {len(ext_before)} tokens verified)")


patch_pipeline()
patch_device()
print("ayn_thor_a740_stability.py: done")
