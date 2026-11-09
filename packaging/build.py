"""
WeBot.exe 打包脚本
用法：python packaging/build.py
将 launcher.py 打包为单个可执行文件。
"""
import subprocess
import shutil
import sys
import os

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING_DIR = os.path.join(PROJECT_ROOT, "packaging")
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")


def check_pyinstaller():
    """检查 PyInstaller 是否已安装。"""
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return False


def build_exe():
    """打包 launcher.py 为 WeBot.exe。"""
    if not check_pyinstaller():
        print("[错误] 请先安装 PyInstaller：pip install pyinstaller")
        sys.exit(1)

    launcher_path = os.path.join(PACKAGING_DIR, "launcher.py")
    icon_path = os.path.join(PACKAGING_DIR, "icon.ico")

    build_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "WeBot",
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(PROJECT_ROOT, "build"),
        "--specpath", os.path.join(PROJECT_ROOT, "build"),
        "--clean",
        "--noconfirm",
    ]

    # 如果存在图标文件，则添加图标参数
    if os.path.exists(icon_path):
        build_cmd += ["--icon", icon_path]

    # 保留控制台（run.ps1 需要交互：用户输入 y/N）
    build_cmd.append(launcher_path)

    print(f"[构建] 正在打包 WeBot.exe ...")
    print(f"  命令: {' '.join(build_cmd)}")
    result = subprocess.run(build_cmd, cwd=PROJECT_ROOT)

    if result.returncode != 0:
        print("[错误] 打包失败")
        sys.exit(1)

    exe_path = os.path.join(DIST_DIR, "WeBot.exe")
    if os.path.exists(exe_path):
        # 复制到项目根目录
        dest_path = os.path.join(PROJECT_ROOT, "WeBot.exe")
        shutil.copy2(exe_path, dest_path)
        print(f"\n[完成] 打包成功！")
        print(f"  exe 位置: {dest_path}")
        print(f"  大小: {os.path.getsize(dest_path) / 1024 / 1024:.1f} MB")
    else:
        print("[错误] 未找到生成的 exe")
        sys.exit(1)


def main():
    print("=" * 50)
    print("  WeBot 打包工具")
    print("=" * 50)
    build_exe()
    print("\n[提示] 打包完成后可用 Inno Setup 打开 packaging/installer.iss 制作安装包")


if __name__ == "__main__":
    main()
