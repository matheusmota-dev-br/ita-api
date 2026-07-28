#!/usr/bin/env python3

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

from src.constants import Architecture, OperatingSystem
from src.utils.get_arch import get_arch
from src.utils.get_os import get_os

CHROMEDRIVER_LATEST_URL = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"

# Nomes de plataforma usados pela API (diferentes do OS_ARCH_MAP)
# Linux: API só oferece linux64; em ARM64 usamos linux64 (ex.: emulação no Docker)
OS_ARCH_TO_API_PLATFORM = {
  OperatingSystem.MACOS: {Architecture.X64: "mac-x64", Architecture.ARM64: "mac-arm64"},
  OperatingSystem.LINUX: {Architecture.X64: "linux64", Architecture.ARM64: "linux64"},
  OperatingSystem.WINDOWS: {Architecture.X64: "win64", Architecture.ARM64: "win64"},
}


def _parse_major_version(version_output: str) -> str:
  version = version_output.strip().split()[-1]
  return version.split(".")[0]


def _get_chrome_version_macos() -> str | None:
  chrome_paths = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ]

  for path in chrome_paths:
    if os.path.exists(path):
      result = subprocess.run(
        [path, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
      )
      if result.returncode == 0:
        return _parse_major_version(result.stdout)
  return None


def _get_chrome_version_linux() -> str | None:
  for binary in ("google-chrome", "chromium", "chromium-browser"):
    try:
      result = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
      )
      if result.returncode == 0:
        return _parse_major_version(result.stdout)
    except FileNotFoundError:
      continue
  return None


def _get_chrome_version_windows() -> str | None:
  import winreg

  registry_paths = [
    (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
  ]

  for root, path in registry_paths:
    try:
      with winreg.OpenKey(root, path) as key:
        version, _ = winreg.QueryValueEx(key, "version")
        return version.split(".")[0]
    except FileNotFoundError:
      continue
  return None


def _get_chrome_version() -> str | None:
  try:
    os_ = get_os()

    if os_ == OperatingSystem.MACOS:
      return _get_chrome_version_macos()
    if os_ == OperatingSystem.LINUX:
      return _get_chrome_version_linux()
    if os_ == OperatingSystem.WINDOWS:
      return _get_chrome_version_windows()

  except (OSError, subprocess.SubprocessError) as e:
    print(f"Error detecting Chrome version: {e}", file=sys.stderr)

  return None


def _get_stable_channel_data():
  """Baixa o JSON e retorna (version, chromedriver_downloads_list)."""
  try:
    response = requests.get(CHROMEDRIVER_LATEST_URL, timeout=10)
    response.raise_for_status()
    data = response.json()
    stable = data.get("channels", {}).get("Stable", {})
    version = stable.get("version")
    downloads = stable.get("downloads", {}).get("chromedriver", [])
    return version, downloads
  except (requests.RequestException, ValueError) as e:
    print(f"Error getting Chrome for Testing data: {e}", file=sys.stderr)
    return None, []


def _get_chromedriver_url_for_platform(downloads: list) -> tuple[str, str]:
  """Escolhe URL e extensão do ChromeDriver a partir da lista de downloads do JSON."""
  os_ = get_os()
  arch = get_arch()
  if not os_:
    raise RuntimeError("Could not detect operating system")
  api_platform = OS_ARCH_TO_API_PLATFORM.get(os_, {}).get(arch, "linux64")
  for item in downloads:
    if item.get("platform") == api_platform:
      url = item.get("url")
      if url:
        ext = "zip" if url.endswith(".zip") else "tar.gz"
        return url, ext
  raise RuntimeError(f"No ChromeDriver download for platform: {api_platform}")


def download_file(url, filepath):
  print(f"Downloading ChromeDriver from: {url}")

  response = requests.get(url, stream=True, timeout=30)
  response.raise_for_status()

  total_size = int(response.headers.get("content-length", 0)) or None

  with open(filepath, "wb") as f:
    with tqdm(total=total_size, unit="B", unit_scale=True, desc="chromedriver") as pbar:
      for chunk in response.iter_content(chunk_size=8192):
        if chunk:
          f.write(chunk)
          pbar.update(len(chunk))

  print("Download completed.")
  return filepath


def extract_chromedriver(archive_path, extract_to, ext):
  print(f"Extracting ChromeDriver from {archive_path}...")

  if ext == "zip":
    with zipfile.ZipFile(archive_path, "r") as zip_ref:
      zip_ref.extractall(extract_to)
  elif ext == "tar.gz":
    with tarfile.open(archive_path, "r:gz") as tar_ref:
      tar_ref.extractall(extract_to)

  os.remove(archive_path)

  for root, _, files in os.walk(extract_to):
    for file in files:
      if file == "chromedriver" or file == "chromedriver.exe":
        chromedriver_path = os.path.join(root, file)
        final_path = os.path.join(extract_to, file)
        if chromedriver_path != final_path:
          if os.path.exists(final_path):
            os.remove(final_path)
          os.rename(chromedriver_path, final_path)

        # Remove apenas a pasta extraída do zip (ex: chromedriver-mac-arm64),
        # não o resto do conteúdo de extract_to
        if os.path.exists(root) and os.path.realpath(root) != os.path.realpath(
          extract_to
        ):
          shutil.rmtree(root)

        return final_path

  return None


def make_executable(filepath):
  if platform.system() != "windows":
    os.chmod(filepath, 0o755)


def main():
  parser = argparse.ArgumentParser(description="Download ChromeDriver")
  parser.add_argument(
    "--force",
    action="store_true",
    help="Replace existing ChromeDriver without prompting (for non-interactive/Docker)",
  )
  args = parser.parse_args()

  print("=" * 60)
  print("Download ChromeDriver")
  print("=" * 60)

  project_root = Path(__file__).parent.parent
  chromedriver_path = project_root / "chromedriver"

  if chromedriver_path.exists():
    if not args.force:
      response = input(
        f"ChromeDriver already exists in {chromedriver_path}. Replace it? (y/N): "
      )
      if response.lower() != "y":
        print("Operation cancelled.")
        return
    os.remove(chromedriver_path)

  chrome_version = _get_chrome_version()
  if chrome_version:
    print(f"Chrome version detected: {chrome_version}")

  print("Getting latest ChromeDriver version...")
  latest_version, chromedriver_downloads = _get_stable_channel_data()

  if not latest_version or not chromedriver_downloads:
    print("Error getting ChromeDriver version or downloads list", file=sys.stderr)
    sys.exit(1)

  print(f"Latest ChromeDriver version: {latest_version}")

  try:
    download_url, ext = _get_chromedriver_url_for_platform(chromedriver_downloads)
  except RuntimeError as e:
    print(f"Error getting download URL: {e}", file=sys.stderr)
    sys.exit(1)

  temp_file = project_root / f"chromedriver_temp.{ext}"

  try:
    download_file(download_url, temp_file)

    chromedriver_extracted = extract_chromedriver(temp_file, project_root, ext)

    if chromedriver_extracted:
      if chromedriver_extracted != str(chromedriver_path):
        if chromedriver_path.exists():
          os.remove(chromedriver_path)
        os.rename(chromedriver_extracted, chromedriver_path)

      make_executable(chromedriver_path)

      print("\n✓ ChromeDriver downloaded successfully!")
      print(f"  Location: {chromedriver_path}")
      print(f"  Version: {latest_version}")
    else:
      print("Error extracting ChromeDriver.", file=sys.stderr)
      sys.exit(1)

  except requests.exceptions.RequestException as e:
    print(f"Error downloading ChromeDriver: {e}", file=sys.stderr)
    sys.exit(1)
  except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
    print(f"Error writing or extracting ChromeDriver: {e}", file=sys.stderr)
    sys.exit(1)
  finally:
    if temp_file.exists():
      os.remove(temp_file)


if __name__ == "__main__":
  main()
