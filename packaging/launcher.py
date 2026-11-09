"""
WeBot 启动器
打包成 exe 后，双击即调用同目录下的 run.ps1。
仅作为快捷方式的入口，不包含任何业务逻辑。
"""
import os
import sys
import subprocess


def main():
    # exe 所在目录（打包后）或脚本所在目录（开发时）
    if getattr(sys, 'frozen', False):
        executable_dir = os.path.dirname(sys.executable)
    else:
        executable_dir = os.path.dirname(os.path.abspath(__file__))

    # run.ps1 在项目根目录（exe 放在根目录时直接同级；放在 packaging/ 时往上一级）
    run_script_path = os.path.join(executable_dir, "run.ps1")
    if not os.path.exists(run_script_path):
        run_script_path = os.path.join(os.path.dirname(executable_dir), "run.ps1")

    if not os.path.exists(run_script_path):
        input("错误：找不到 run.ps1，请确认文件完整性。按回车退出...")
        sys.exit(1)

    # 使用 PowerShell 执行 run.ps1，工作目录设为 run.ps1 所在目录
    working_directory = os.path.dirname(run_script_path)
    subprocess.call(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", run_script_path],
        cwd=working_directory,
    )


if __name__ == "__main__":
    main()
