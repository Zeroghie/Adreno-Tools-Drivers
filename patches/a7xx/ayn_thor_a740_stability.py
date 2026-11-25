import re
from mesa_edit import replace

DEVICE = 'src/freedreno/vulkan/tu_device.cc'
IMAGE = 'src/freedreno/vulkan/tu_image.cc'
PIPELINE = 'src/freedreno/vulkan/tu_pipeline.cc'

# 1. Aggancia process_heap_usage.py di StevenMXZ e limita heapBudget a 6 GiB su 12GB RAM
replace(
    DEVICE,
    'memory_budget_props->heapUsage = p_atomic_read(&tu_process_heap_used);',
    'memory_budget_props->heapUsage = p_atomic_read(&tu_process_heap_used);\n'
    '   if (memory_budget_props->heapBudget > 6ULL * 1024 * 1024 * 1024)\n'
    '      memory_budget_props->heapBudget = 6ULL * 1024 * 1024 * 1024;',
    'AYN Thor Pro: cap heapBudget to 6 GiB to prevent 12GB OOM',
)

# 2. Disattiva UBWC sulle immagini MUTABLE (non-A8xx) e STORAGE in tu_image.cc (fix quadratini A740 senza noubwc)
replace(
    IMAGE,
    '   if ((pCreateInfo->flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT) &&\n'
    '       !vk_format_is_depth_or_stencil(image->vk.format)) {',
    '   if (pCreateInfo->usage & VK_IMAGE_USAGE_STORAGE_BIT)\n'
    '      ubwc_enabled = false;\n'
    '   if ((pCreateInfo->flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT) &&\n'
    '       !vk_format_is_depth_or_stencil(image->vk.format)) {\n'
    '      if (!mutable_ubwc_fc)\n'
    '         ubwc_enabled = false;',
    'AYN Thor Pro: disable UBWC on non-A8xx mutable/storage images to fix A740 block artifacts',
)

# 3. Riduce i blocchi del sub-allocatore BO a 32MB in tu_device.cc
with open(DEVICE, 'r', encoding='utf-8') as f:
    dev_src = f.read()

new_dev_src, count = re.subn(
    r'(tu_bo_suballoc_init\s*\([^;]+?,\s*)(128|64)(\s*\*\s*1024\s*\*\s*1024)',
    r'\g<1>32\g<3>',
    dev_src,
)
if count > 0:
    with open(DEVICE, 'w', encoding='utf-8') as f:
        f.write(new_dev_src)
    print(f'{DEVICE}: AYN Thor Pro suballoc block size reduced to 32MB')

# 4. Limitatore automatico thread shader leggendo /proc/meminfo (se MemAvailable < 3.5 GiB -> max 2 thread)
with open(PIPELINE, 'r', encoding='utf-8') as f:
    pip_src = f.read()

meminfo_helper = """
#include <stdio.h>
#include <string.h>
static uint32_t ayn_thor_clamp_compiler_threads(uint32_t requested) {
   FILE *fp = fopen("/proc/meminfo", "r");
   if (!fp) return requested > 2 ? 2 : requested;
   char line[128];
   unsigned long avail_kb = 0;
   while (fgets(line, sizeof(line), fp)) {
      if (sscanf(line, "MemAvailable: %lu kB", &avail_kb) == 1)
         break;
   }
   fclose(fp);
   if (avail_kb > 0 && avail_kb < 3670016UL) /* < 3.5 GiB liberi */
      return requested > 2 ? 2 : requested;
   return requested > 4 ? 4 : requested;
}
"""

if 'ayn_thor_clamp_compiler_threads' not in pip_src:
    # Inserisce l'helper subito prima della prima funzione void/VkResult in tu_pipeline.cc
    pip_mod, h_cnt = re.subn(
        r'(\n(?:static\s+)?(?:void|VkResult)\s+tu_)',
        '\n' + meminfo_helper + r'\1',
        pip_src,
        count=1,
    )
    # Aggancia util_queue_init se presente per limitare num_threads
    pip_mod, q_cnt = re.subn(
        r'(util_queue_init\s*\([^,]+,\s*[^,]+,\s*[^,]+,\s*)([^,]+)(,\s*[^)]+\))',
        r'\1ayn_thor_clamp_compiler_threads(\2)\3',
        pip_mod,
    )
    if h_cnt > 0 and q_cnt > 0:
        with open(PIPELINE, 'w', encoding='utf-8') as f:
            f.write(pip_mod)
        print(f'{PIPELINE}: AYN Thor Pro /proc/meminfo dynamic shader thread limiter applied')
    else:
        print(f'{PIPELINE}: util_queue_init pattern not matched, skipping dynamic thread clamp safely')

