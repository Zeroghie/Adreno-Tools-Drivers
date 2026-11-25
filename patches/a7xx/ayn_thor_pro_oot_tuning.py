#!/usr/bin/env python3
"""
AYN Thor Pro (Snapdragon 8 Gen 2 / Adreno 740) tuning for the unified Turnip driver
(Mesa main, a6xx + a7xx + a8xx in one libvulkan_freedreno.so). Run from the Mesa root,
AFTER patches/common/tu-vauzi-mutable-ubwc.patch has been applied.
"""
import os
import re
import sys

DISABLE_UBWC_STORAGE = True
DISABLE_UBWC_MUTABLE = True

TU = "src/freedreno/vulkan"
IMAGE_CC = f"{TU}/tu_image.cc"
PIPELINE_CC = f"{TU}/tu_pipeline.cc"

MARK_STORAGE = "AYN-UBWC-STORAGE"
MARK_MUTABLE = "AYN-UBWC-MUTABLE"
LIMIT_MARKER = "AYN-A740-STABILITY"
ENTRYPOINTS = ("tu_CreateGraphicsPipelines", "tu_CreateComputePipelines")

EXT_RE = re.compile(r"\b(VK_(?:KHR|EXT|VALVE|QCOM|GOOGLE|MESA)_\w+|\.(?:KHR|EXT|VALVE|QCOM)_\w+)")
LITERALS = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.S)

RESULTS = []

LIMITER = r'''
/* AYN-A740-STABILITY BEGIN: cap concurrent pipeline compilations (default 4) */
#include <condition_variable>
#include <cstdlib>
#include <mutex>

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
      int v = env ? atoi(env) : 0;
      limit = v > 0 ? v : 4;
   }

   std::mutex mtx;
   std::condition_variable cv;
   int active = 0;
   int limit = 4;
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

GUARD_LINE = "\n   ayn_compile_guard ayn_guard_; /* AYN-A740-STABILITY */"


def report(patch, status, detail=""):
    RESULTS.append((patch, status, detail))
    print(f"  [{status}] {patch}: {detail}", flush=True)


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        print(f"  WARNING: cannot read {path}: {e}", file=sys.stderr)
        return None


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def mask(text):
    return LITERALS.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def balance(masked, o, c):
    return masked.count(o) - masked.count(c)


def match_close(masked, open_idx):
    depth = 0
    for i in range(open_idx, len(masked)):
        ch = masked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def find_function(masked, name):
    for m in re.finditer(rf"\b{name}\s*\(([^;{{}}]*)\)\s*(?:const\s*)?\{{", masked):
        ls = masked.rfind("\n", 0, m.start()) + 1
        if re.search(r"[(=]|\breturn\b", masked[ls:m.start()]):
            continue
        depth, i = 1, m.end()
        while i < len(masked) and depth:
            c = masked[i]
            depth += (c == "{") - (c == "}")
            i += 1
        if depth:
            return None
        return m.group(1), m.end(), i, m.start()
    return None


def pp_depth(masked, pos):
    seg = masked[:pos]
    return (len(re.findall(r"^[ \t]*#[ \t]*if", seg, re.M))
            - len(re.findall(r"^[ \t]*#[ \t]*endif", seg, re.M)))


def insert_after_includes(text, snippet):
    masked = mask(text)
    first_def = re.search(r"^(?:static|struct|namespace|VKAPI_ATTR|template|enum|class|typedef|extern)\b",
                          masked, re.M)
    limit = first_def.start() if first_def else len(text)
    incs = [m for m in re.finditer(r"^[ \t]*#[ \t]*include\b.*\n", masked, re.M) if m.end() <= limit]
    for m in reversed(incs):
        if pp_depth(masked, m.end()) == 0:
            return text[:m.end()] + snippet + text[m.end():]
    return None


def sane(before_raw, before_masked, after_raw, after_masked):
    if sorted(EXT_RE.findall(after_raw)) != sorted(EXT_RE.findall(before_raw)):
        return "Vulkan extension tokens changed"
    if balance(after_masked, "{", "}") != balance(before_masked, "{", "}"):
        return "brace balance changed"
    if balance(after_masked, "(", ")") != balance(before_masked, "(", ")"):
        return "parenthesis balance changed"
    return None


P_STORAGE = "1/3 UBWC off for STORAGE images, legacy a7xx (tu_image.cc: ubwc_possible)"


def edit_storage(text):
    if not DISABLE_UBWC_STORAGE:
        return text, "SKIPPED", "disabled by DISABLE_UBWC_STORAGE"
    if MARK_STORAGE in text:
        return text, "ALREADY", "marker present"
    fn = find_function(mask(text), "ubwc_possible")
    if not fn:
        return text, "SKIPPED", "definition of ubwc_possible not found"
    params, bs = fn[0], fn[1]
    info = re.search(r"\bfd_dev_info\s*\*\s*(\w+)", params)
    uses = re.findall(r"\bVkImageUsageFlags\s+(\w+)", params)
    if not info or not uses:
        return text, "SKIPPED", f"unexpected signature: {' '.join(params.split())[:120]}"
    usage = f"({uses[0]} | {uses[1]})" if len(uses) > 1 else uses[0]
    i = info.group(1)
    snippet = (
        f"\n   /* {MARK_STORAGE}: legacy a7xx (A730/A740) - no UBWC for storage images */\n"
        f"   if (({usage} & VK_IMAGE_USAGE_STORAGE_BIT) &&\n"
        f"       {i}->chip == 7 && !{i}->props.ubwc_all_formats_compatible &&\n"
        f"       !getenv(\"TU_AYN_KEEP_UBWC\") && !getenv(\"TU_AYN_KEEP_UBWC_STORAGE\"))\n"
        f"      return false;\n"
    )
    return text[:bs] + snippet + text[bs:], "APPLIED", \
        f"early return false when {usage} has STORAGE_BIT on chip 7 without all-formats-compatible UBWC"


P_MUTABLE = "2/3 UBWC off for mutable images with incompatible view formats, legacy a7xx (tu_image_init)"


def edit_mutable(text):
    if not DISABLE_UBWC_MUTABLE:
        return text, "SKIPPED", "disabled by DISABLE_UBWC_MUTABLE"
    if MARK_MUTABLE in text:
        return text, "ALREADY", "marker present"
    masked = mask(text)
    fn = find_function(masked, "tu_image_init")
    if not fn:
        return text, "SKIPPED", "definition of tu_image_init not found"
    params, bs, be, _ = fn
    dev = re.search(r"\btu_device\s*\*\s*(\w+)", params)
    mbody = masked[bs:be]
    if not dev or not re.search(r"\bubwc_enabled\b", mbody):
        return text, "SKIPPED", "device parameter or ubwc_enabled variable not found in tu_image_init"
    m = re.search(r"\bif\s*\(\s*!\s*tu6_mutable_format_list_ubwc_compatible\s*\(", mbody)
    if not m:
        for k, ln in enumerate(text.split("\n"), 1):
            if re.search(r"MUTABLE_FORMAT_BIT|mutable_ubwc_fc|ubwc_compatible", ln):
                print(f"    {IMAGE_CC}:{k}: {ln.strip()[:120]}")
        return text, "SKIPPED", "'if (!tu6_mutable_format_list_ubwc_compatible(...))' block not found"
    close = match_close(mbody, mbody.index("(", m.start()))
    brace = re.match(r"\s*\{", mbody[close + 1:]) if close >= 0 else None
    if not brace:
        return text, "SKIPPED", "could not locate the opening brace of the mutable block"
    at = bs + close + 1 + brace.end()
    d = dev.group(1)
    snippet = (
        f"\n         /* {MARK_MUTABLE}: legacy a7xx (A730/A740) has no all-formats-compatible UBWC, so\n"
        f"          * do not compress mutable images whose view formats are not UBWC compatible. */\n"
        f"         if ({d}->physical_device->info->chip == 7 &&\n"
        f"             !{d}->physical_device->info->props.ubwc_all_formats_compatible &&\n"
        f"             !getenv(\"TU_AYN_KEEP_UBWC\") && !getenv(\"TU_AYN_KEEP_UBWC_MUTABLE\"))\n"
        f"            ubwc_enabled = false;\n"
    )
    return text[:at] + snippet + text[at:], "APPLIED", \
        "ubwc_enabled = false inside the 'view formats not UBWC compatible' branch, chip 7 only"


def patch_ubwc():
    content = read(IMAGE_CC)
    if content is None:
        report(P_STORAGE, "SKIPPED", "tu_image.cc not found")
        report(P_MUTABLE, "SKIPPED", "tu_image.cc not found")
        return
    new = content
    outcomes = []
    for name, fn in ((P_STORAGE, edit_storage), (P_MUTABLE, edit_mutable)):
        try:
            new, status, detail = fn(new)
        except Exception as e:
            status, detail = "ERROR", f"{type(e).__name__}: {e}"
        outcomes.append((name, status, detail))

    if new != content:
        inc = insert_after_includes(new, "#include <stdlib.h> /* AYN-UBWC */\n")
        if inc is not None:
            new = inc
        why = sane(content, mask(content), new, mask(new))
        if why:
            for name, status, _ in outcomes:
                report(name, "SKIPPED" if status == "APPLIED" else status,
                       f"safety check failed ({why}); tu_image.cc not modified")
            return
        write(IMAGE_CC, new)
    for name, status, detail in outcomes:
        report(name, status, detail)


P_CAP = "3/3 pipeline compile cap = 4 (tu_pipeline.cc)"


def patch_compile_cap():
    content = read(PIPELINE_CC)
    if content is None:
        return report(P_CAP, "SKIPPED", "file not found")
    masked0 = mask(content)

    had_limiter = f"{LIMIT_MARKER} BEGIN" in content
    new = content
    if not had_limiter:
        with_limiter = insert_after_includes(new, LIMITER)
        if with_limiter is None:
            return report(P_CAP, "SKIPPED", "no top-level #include block found")
        new = with_limiter

    guarded, added = 0, 0
    for name in ENTRYPOINTS:
        fn = find_function(mask(new), name)
        if not fn:
            print(f"  WARNING: {PIPELINE_CC}: definition of {name} not found", file=sys.stderr)
            continue
        bs = fn[1]
        if "ayn_compile_guard ayn_guard_" in new[bs:bs + 300]:
            guarded += 1
            continue
        new = new[:bs] + GUARD_LINE + new[bs:]
        guarded += 1
        added += 1

    if guarded == 0:
        return report(P_CAP, "SKIPPED", "no pipeline entrypoint found; file not modified")
    if new == content:
        return report(P_CAP, "ALREADY",
                      f"{guarded}/{len(ENTRYPOINTS)} entrypoints already guarded "
                      f"(possibly by ayn_thor_a740_stability.py)")
    why = sane(content, masked0, new, mask(new))
    if why:
        return report(P_CAP, "SKIPPED", f"safety check failed ({why}); file not modified")
    write(PIPELINE_CC, new)
    report(P_CAP, "APPLIED",
           f"{added} new guard(s), {guarded}/{len(ENTRYPOINTS)} entrypoints guarded, "
           f"limiter {'reused' if had_limiter else 'added'}; max 4 concurrent calls "
           f"(env TU_MAX_PARALLEL_PIPELINE_COMPILES=N)")


def main():
    print(f"ayn_thor_pro_oot_tuning.py: Mesa tree = {os.getcwd()}", flush=True)
    for fn in (patch_ubwc, patch_compile_cap):
        try:
            fn()
        except Exception as e:
            report(fn.__name__, "ERROR", f"{type(e).__name__}: {e}")

    print("\n  ===== AYN Thor Pro tuning summary =====")
    for patch, status, _ in RESULTS:
        print(f"  {status:<8} {patch}")
    bad = [r for r in RESULTS if r[1] in ("SKIPPED", "ERROR")]
    print("ayn_thor_pro_oot_tuning.py: done" + (f" ({len(bad)} skipped/error)" if bad else ""))
    if os.environ.get("AYN_STRICT") == "1" and bad:
        sys.exit(1)


main()
