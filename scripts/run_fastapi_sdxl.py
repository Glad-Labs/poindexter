#!/usr/bin/env python3.13
"""Run FastAPI with SDXL support - with Flash Attention disabled for RTX 5090"""
import subprocess
import sys
import os

# Disable Flash Attention (incompatible with RTX 5090 sm_120)
os.environ['CUDA_DISABLE_FLASH_ATTENTION'] = '1'
os.environ['TRANSFORMERS_DISABLE_FLASH_ATTN'] = '1'

# Use Python 3.13 with CUDA PyTorch
python_exe = r"C:\Users\mattm\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"

# Run Uvicorn
subprocess.run([
    python_exe, 
    "-m", "uvicorn",
    "main:app",
    "--host", "0.0.0.0",
    "--port", "8000",
    "--reload"
], cwd=r"C:\Users\mattm\glad-labs-website\src\cofounder_agent")
