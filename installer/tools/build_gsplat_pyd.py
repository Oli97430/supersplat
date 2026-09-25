# Compile gsplat's CUDA extension into a standalone .pyd for the installer.
# Run by build-gsplat-pyd.ps1 inside an MSVC + CUDA environment, with the
# target venv's python and TORCH_CUDA_ARCH_LIST already set.
#
#   python build_gsplat_pyd.py <build_dir>

import glob
import os
import sys

import gsplat
import torch
from torch.utils.cpp_extension import load

build_dir = os.path.abspath(sys.argv[1])
os.makedirs(build_dir, exist_ok=True)

csrc = os.path.join(os.path.dirname(gsplat.__file__), "cuda", "csrc")
sources = glob.glob(os.path.join(csrc, "*.cu")) + glob.glob(os.path.join(csrc, "*.cpp"))
if not sources:
    sys.exit(f"no gsplat sources under {csrc}")

print(f"torch {torch.__version__} (CUDA {torch.version.cuda}), gsplat {gsplat.__version__}")
print(f"TORCH_CUDA_ARCH_LIST={os.environ.get('TORCH_CUDA_ARCH_LIST')}")

# Same flags as gsplat's own JIT path (plus the MSVC preprocessor fix the
# installer already patches into _backend.py for CUDA 12.8+/13.x CCCL).
mod = load(
    name="gsplat_cuda",
    sources=sources,
    extra_cflags=["-O3"],
    extra_cuda_cflags=[
        "-O3",
        "--use_fast_math",
        "-Xcompiler",
        "/Zc:preprocessor",
        "-DCCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING",
    ],
    extra_include_paths=[csrc],
    build_directory=build_dir,
    verbose=True,
)
print(f"BUILT {mod.__file__}")
