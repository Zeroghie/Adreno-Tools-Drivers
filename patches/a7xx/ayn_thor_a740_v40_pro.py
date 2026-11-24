#!/usr/bin/env python3
import os
import re
import sys

def read_file(path):
    if not os.path.exists(path):
        print(f"[WARN] File non trovato: {path}")
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def patch_tu_device():
    path = "src/freedreno/vulkan/tu_device.cc"
    content = read_file(path)
    if not content:
        return

    modified = False

    # 1. Riduce la dimensione dei blocchi del sub-allocatore BO per evitare accumuli di RAM su Android 12GB
    suballoc_pattern = re.compile(r'(tu_bo_suballoc_init\s*\([^;]+?,\s*)(128|64)(\s*\*\s*1024\s*\*\s*1024)')
    if suballoc_pattern.search(content):
        content = suballoc_pattern.sub(r'\g<1>32\g<3>', content)
        print("[OK] Sub-allocatore BO ridotto a 32MB in tu_device.cc")
        modified = True
    else:
        print("[INFO] Pattern sub-allocatore non trovato o già modificato in tu_device.cc")

    # 2. Tetto massimo a 6 GB (6144 MB) per heapBudget / memoryHeaps così l'emulatore svuota prima la cache
    budget_hook = """
   /* AYN Thor Pro 12GB OOM Prevention: cap reported VRAM budget to 6GB */
   const uint64_t ayn_max_vram = 6ULL * 1024 * 1024 * 1024;
   for (uint32_t i = 0; i < pMemoryProperties->memoryProperties.memoryHeapCount; i++) {
      if (pMemoryProperties->memoryProperties.memoryHeaps[i].size > ayn_max_vram) {
         pMemoryProperties->memoryProperties.memoryHeaps[i].size = ayn_max_vram;
      }
   }
"""
    # Inserimento sicuro dentro tu_GetPhysicalDeviceMemoryProperties2 prima del return finale
    mem_props_pattern = re.compile(
        r'(VKAPI_ATTR\s+void\s+VKAPI_CALL\s+tu_GetPhysicalDeviceMemoryProperties2\s*\([^)]+\)\s*\{.*?)(\n\})',
        re.DOTALL
    )
    if "ayn_max_vram" not in content and mem_props_pattern.search(content):
        budget_ext_hook = """
   vk_foreach_struct(ext, pMemoryProperties->pNext) {
      if (ext->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT) {
         VkPhysicalDeviceMemoryBudgetPropertiesEXT *budget =
            (VkPhysicalDeviceMemoryBudgetPropertiesEXT *)ext;
         for (uint32_t i = 0; i < VK_MAX_MEMORY_HEAPS; i++) {
            if (budget->heapBudget[i] > ayn_max_vram)
               budget->heapBudget[i] = ayn_max_vram;
         }
      }
   }
"""
        content = mem_props_pattern.sub(r'\1' + budget_hook + budget_ext_hook + r'\2', content, count=1)
        print("[OK] Tetto Memory Budget 6GB applicato in tu_device.cc")
        modified = True
    else:
        print("[INFO] Hook Memory Budget non inserito o già presente in tu_device.cc")

    if modified:
        write_file(path, content)

def patch_tu_image():
    path = "src/freedreno/vulkan/tu_image.cc"
    content = read_file(path)
    if not content:
        return

    if "ayn_ubwc_selective" in content:
        print("[INFO] Patch UBWC selettiva già presente in tu_image.cc")
        return

    # 3. Disattiva UBWC SOLO su immagini STORAGE o MUTABLE_FORMAT (causa dei quadratini su Adreno 740)
    ubwc_pattern = re.compile(
        r'(tu6_ubwc_possible\s*\([^)]*\)\s*\{)',
        re.DOTALL
    )

    ubwc_guard = """
   /* ayn_ubwc_selective: disable UBWC only on storage/mutable images to prevent A740 block artifacts */
   if (info->usage & VK_IMAGE_USAGE_STORAGE_BIT)
      return false;
   if (info->flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT)
      return false;
"""

    if ubwc_pattern.search(content):
        # Verifica che il parametro si chiami 'info' prima di inserire
        func_header = ubwc_pattern.search(content).group(1)
        if "VkImageCreateInfo" in func_header and "*info" in func_header:
            content = ubwc_pattern.sub(r'\1' + ubwc_guard, content, count=1)
            write_file(path, content)
            print("[OK] Patch UBWC selettiva (STORAGE/MUTABLE) applicata in tu_image.cc")
        else:
            print("[WARN] Firma di tu6_ubwc_possible diversa dal previsto, salto per sicurezza")
    else:
        print("[INFO] Funzione tu6_ubwc_possible non trovata in tu_image.cc")

if __name__ == "__main__":
    print("=== Applicazione patch AYN Thor Pro (Adreno 740 / 12GB RAM) per Turnip V40 ===")
    patch_tu_device()
    patch_tu_image()
    print("=== Completato ===")
  
