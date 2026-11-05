"""Скачивает JDK, Android cmdline-tools и Gradle для локальной сборки APK."""

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

TOOLS = Path("android-build/tools")
DOWNLOADS = Path("android-build/downloads")

JDK_URL = (
    "https://github.com/adoptium/temurin17-binaries/releases/download/"
    "jdk-17.0.21%2B9/OpenJDK17U-jdk_x64_windows_hotspot_17.0.21_9.zip"
)
CMDLINE_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
GRADLE_URL = "https://services.gradle.org/distributions/gradle-8.5-bin.zip"


def fetch(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"уже есть: {dest.name} ({dest.stat().st_size // 1048576} МБ)", flush=True)
        return dest
    print(f"качаю {dest.name}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total and done % (20 << 20) < (1 << 20):
                print(f"  {done // 1048576}/{total // 1048576} МБ", flush=True)
    print(f"готово: {dest.name}", flush=True)
    return dest


def unzip(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dest)


def main() -> int:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    TOOLS.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    jdk_zip = fetch(JDK_URL, DOWNLOADS / "jdk17.zip")
    if not (TOOLS / "jdk17" / "bin" / "java.exe").exists():
        tmp = TOOLS / "_jdk_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        unzip(jdk_zip, tmp)
        inner = next(p for p in tmp.iterdir() if p.is_dir())
        shutil.move(str(inner), str(TOOLS / "jdk17"))
        shutil.rmtree(tmp, ignore_errors=True)
        print("JDK распакован", flush=True)

    cm_zip = fetch(CMDLINE_URL, DOWNLOADS / "cmdline-tools.zip")
    sdk = TOOLS / "android-sdk"
    if not (sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat").exists():
        tmp = TOOLS / "_cm_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        unzip(cm_zip, tmp)
        (sdk / "cmdline-tools").mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / "cmdline-tools"), str(sdk / "cmdline-tools" / "latest"))
        shutil.rmtree(tmp, ignore_errors=True)
        print("cmdline-tools распакованы", flush=True)

    gr_zip = fetch(GRADLE_URL, DOWNLOADS / "gradle.zip")
    if not (TOOLS / "gradle-8.5" / "bin" / "gradle.bat").exists():
        unzip(gr_zip, TOOLS)
        print("Gradle распакован", flush=True)

    print("ВСЁ ГОТОВО", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
