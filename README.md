# 🎮 Turnip V40 — AYN Thor Pro Edition (Adreno 740)

[![Device](https://shields.io)](https://github.com)
[![SoC](https://shields.io)](https://github.com)
[![GPU](https://shields.io)](https://github.com)
[![Vulkan](https://shields.io)](https://github.com)

> **Custom Mesa 27.0.0-devel (Turnip v27.0.0-R3 / V40 base) driver compiled via NDK r29 & KGSL, specifically tuned for stability and memory management on 12 GB RAM devices.**

### 👉 [**⬇️ DOWNLOAD LATEST RELEASE (ZIP)**](https://github.com)


---

## 🚀 Key Modifications (`ayn_thor_a740_stability.py`)

Standard V40 drivers aggressively allocate system memory and spawn unrestricted shader compilation threads, leading to `std::bad_alloc` crashes on **12 GB RAM** handhelds during heavy emulation (such as *The Legend of Zelda: Ocarina of Time Remake* on **Banana Native**). This custom build introduces two targeted kernel/driver safeguards while keeping all **131 Vulkan 1.4.367 extensions** intact:

| Component | File Patched | Optimization Applied | Benefit on AYN Thor Pro |
| :--- | :--- | :--- | :--- |
| **Pipeline Compiler** | `tu_pipeline.cc` | Dynamic concurrency limiter reading `/proc/meminfo` (`MemAvailable`), keeping **≥ 2 GiB** reserved for the emulator | Eliminates `std::bad_alloc` aborts during heavy shader compilation |
| **System Heap Cap** | `tu_device.cc` | Scales `tu_get_system_heap_size` return value by `5/6` (~62.5% instead of 75% total RAM) | Prevents VRAM over-allocation and Android OOM kills |
| **Gen8 Isolation** | `tu_device.cc` / `tu_pipeline.cc` | Verifies all `CHIP >= A8XX` paths stay strictly disabled on A740 | Ensures full stability on Adreno 740 (`a7xx_gen2`) |

---

## ⚙️ Recommended Settings for AYN Thor Pro (Banana Native)

To get the best stability and frame pacing on **Snapdragon 8 Gen 2 (12 GB RAM)**, pair this driver with the following settings:

### 1. Environment Variables
```ini
TU_MAX_PARALLEL_PIPELINE_COMPILES=2
EDEN_NX2_VRAM_LIMIT_MIB=2048
MESA_SHADER_CACHE_MAX_SIZE=1G
```
*(Optional: add `TU_DEBUG=noubwc` only if you experience shadow or color-block glitches).*

### 2. Emulator & Console Profile
* **Resolution:** `0.75x` or `1x` (Handheld mode — avoid Docked mode)
* **GPU Accuracy:** `Normal`
* **Shader Compilation:** `Asynchronous` enabled
* **AYN Control Center:** Set performance profile to **High Performance** and fan curve to **Sport / Smart**

---

## 📦 Installation

1. Go to the [**Releases**](https://github.com) page and download the `.zip` archive (**do not extract it**).
2. Open **Banana Native** (or your AdrenoTools-compatible emulator) on your **AYN Thor Pro**.
3. Navigate to **Settings → GPU Driver Manager → Install**, select the downloaded `.zip` file, and activate it.

---

## 🙏 Credits

* Upstream build script & V40 unified base by [**StevenMXZ**](https://github.com)
* Mesa / Freedreno Turnip development team


