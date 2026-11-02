# directory_structure.py

import os
import platform
from pathlib import Path
from enum import Enum


class Platform(Enum):
    WINDOWS = 1
    LINUX = 2
    MACOS = 3
    MSYS = 4
    UNKNOWN = 5


class DirStruct:
    def __init__(self, app_name, current_mode):
        self.app_name = app_name
        self.current_mode = current_mode  # Either "env_mode" or "build_mode"
        self.platform = self.detect_platform()

        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            self.project_root = Path(__file__).parent.parent
        elif self.platform == Platform.WINDOWS:
            self.project_root = Path(__file__).parent.parent  # Add .parent if this file is in a subdirectory
        else:
            self.project_root = Path.cwd() / ""  # Fallback

    def get_current_mode(self):
        return self.current_mode

    def detect_platform(self):
        system = platform.system().lower()
        if 'msys' in system or 'mingw' in system:
            return Platform.MSYS
        elif system == 'windows':
            return Platform.WINDOWS
        elif system == 'linux':
            return Platform.LINUX
        elif system == 'darwin':
            return Platform.MACOS
        return Platform.UNKNOWN

    @staticmethod
    def detect_terminal():
        return (
                os.environ.get('MSYSTEM')
                or os.environ.get('TERM_PROGRAM')
                or os.environ.get('TERM')
                or os.environ.get('SHELL')
                or os.environ.get('COMSPEC', '')
        ).lower()

    @property
    def find_venv(self):
        venv_paths = [
            ".venv/Scripts",
            ".venv/bin",
            "venv/Scripts",
            "venv/bin",
            "env/Scripts",
            "env/bin"
            ]

        for path in venv_paths:
            test_path = None

            if self.current_mode == "env_mode":
                test_path = self.project_root / path
            else:
                # Build mode paths
                if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
                    test_path = Path.home() / ".local/share" / self.app_name / path
                elif self.platform == Platform.WINDOWS:
                    terminal = self.detect_terminal()
                    if terminal in [
                        "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                        "x86_64"
                        ]:
                        test_path = Path.home() / ".local/share" / self.app_name / path
                    elif terminal in ["msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"]:
                        test_path = Path(os.environ['LOCALAPPDATA']) / self.app_name / path
            # Fallback to current directory if no specific path was set
            test_path = test_path or Path.cwd() / path
            # Check if the path exists
            if test_path.exists():
                return path

        return None

    @property
    def bash_dir(self):
        """Returns the appropriate bin  directory that contains bash binary or cmd executable"""
        if self.current_mode == "env_mode":
            return self.project_root / ".config"

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path.home() / "usr/bin"
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path.home() / "usr/bin"
            elif self.detect_terminal() in [
                "msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"
                ]:
                return Path(os.environ['APPDATA'])
        return Path.cwd() / "usr/bin"  # Fallback

    @property
    def shell_executable(self) -> str:
        """Returns the path to the appropriate shell executable (bash/cmd/powershell)."""
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            # Check common bash locations
            possible_paths = [
                Path("/bin/bash"),
                Path("/usr/bin/bash"),
                Path.home() / "usr/bin/bash",
                Path.cwd() / "usr/bin/bash",
                ]
            for path in possible_paths:
                if path.exists():
                    return str(path.resolve())  # Return as absolute path string

            # Fallback to system PATH
            return "bash"

        elif self.platform == Platform.WINDOWS:
            terminal_type = self.detect_terminal()

            # MSYS2/Git Bash/MINGW64
            if terminal_type in ["ucrt64", "clang", "msys", "mingw64", "msys2", "x86_64"]:
                possible_paths = [
                    Path.home() / "usr/bin/bash.exe",
                    Path(os.environ.get("MSYSTEM_PREFIX", "")) / "bin/bash.exe",
                    Path("C:/msys64/usr/bin/bash.exe"),
                    ]
                for path in possible_paths:
                    if path.exists():
                        return str(path.resolve())

                # Fallback to PATH
                return "bash.exe"

            # Windows CMD
            elif terminal_type in ["cmd", "msdos", "ms-dos"]:
                cmd_path = Path(os.environ["COMSPEC"])  # Usually C:\Windows\System32\cmd.exe
                return str(cmd_path.resolve())

            # PowerShell
            elif terminal_type in ["powershell", "pwsh"]:
                ps_path = Path(
                    os.environ["SYSTEMROOT"]
                    ) / "System32/WindowsPowerShell/v1.0/powershell.exe"
                return str(ps_path.resolve())

        # Final fallback
        return "cmd.exe" if self.platform == Platform.WINDOWS else "bash"


    def run_sudo_command(self, s_process, commands):
        """Helper method to run sudo commands reliably."""
        try:
            # Try with preserved environment first
            try:
                return s_process.run(
                    ["sudo", "-E", "--inline"] + commands,
                    stdout=s_process.PIPE,
                    stderr=s_process.PIPE,
                    text=True,
                    shell=False,
                    check=True
                    )
            except s_process.CalledProcessError:
                # Fallback to finding full path
                which_cmd = s_process.run(
                    ["which", commands[0]],
                    stdout=s_process.PIPE,
                    stderr=s_process.PIPE,
                    text=True,
                    check=True
                    )
                cmd_path = which_cmd.stdout.strip()

                return s_process.run(
                    ["sudo", cmd_path] + commands[1:],
                    stdout=s_process.PIPE,
                    stderr=s_process.PIPE,
                    text=True,
                    shell=False,
                    check=True
                    )
        except s_process.CalledProcessError as e:
            print(f"sudo process failed with error: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error for run sudo process command: {str(e)}")
            return None

    @property
    def config_dir(self):
        """Returns the appropriate config directory based on mode"""
        if self.current_mode == "env_mode":
            return self.project_root / ".config"

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path.home() / ".config" / self.app_name
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path.home() / ".config" / self.app_name
            elif self.detect_terminal() in [
                "msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"
                ]:
                return Path(os.environ['APPDATA']) / self.app_name / ".config"
        return Path.cwd() / ".config"  # Fallback

    @property
    def data_dir(self):
        """Returns the appropriate data directory based on mode"""
        if self.current_mode == "env_mode":
            return self.project_root

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path.home() / ".local/share" / self.app_name
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path.home() / ".local/share" / self.app_name
            elif self.detect_terminal() in [
                "msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"
                ]:
                return Path(os.environ['LOCALAPPDATA']) / self.app_name
        return Path.cwd()  # Fallback

    @property
    def docs_dir(self):
        """Returns the appropriate data directory based on mode"""
        if self.current_mode == "env_mode":
            return self.project_root / "docs"

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path.home() / ".local/share" / self.app_name / "docs"
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path.home() / ".local/share" / self.app_name / "docs"
            elif self.detect_terminal() in [
                "msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"
                ]:
                return Path(os.environ['LOCALAPPDATA']) / self.app_name / "docs"
        return Path.cwd()  # Fallback

    @property
    def log_dir(self):
        """Returns the appropriate log directory based on mode"""
        if self.current_mode == "env_mode":
            return self.project_root / ".logs"

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path.home() / ".logs" / self.app_name
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path.home() / ".logs" / self.app_name
            elif self.detect_terminal() in ["msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"]:
                return Path(os.environ['LOCALAPPDATA']) / self.app_name / ".logs"
        return Path.cwd() /  "logs"  # Fallback

    @property
    def cache_dir(self):
        """Returns the appropriate cache directory based on mode"""
        if self.current_mode == "env_mode":
            return self.project_root / ".cache"

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path.home() / ".cache" / self.app_name
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path.home() / ".cache" / self.app_name
            elif self.detect_terminal() in [
                "msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"
                ]:
                return Path(os.environ['LOCALAPPDATA']) / self.app_name / ".cache"
        return Path.cwd() / "cache"  # Fallback

    @property
    def system_config_dir(self):
        """Returns the appropriate system config directory based on mode"""
        if self.current_mode == "env_mode":
            return self.project_root / ".systemconfig"

        # Build mode paths
        if self.platform in (Platform.LINUX, Platform.MACOS, Platform.MSYS):
            return Path("/usr/local/etc") / self.app_name
        elif self.platform == Platform.WINDOWS:
            if self.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
                ]:
                return Path("/usr/local/etc") / self.app_name
            elif self.detect_terminal() in [
                "msdos", "ms-dos", "cmd", "powershell", "windows", "pwsh"
                ]:
                return Path(os.environ['PROGRAMDATA']) / self.app_name
        return Path("/") / self.app_name  # Fallback

    def ensure_dirs_exist(self):
        """Creates all required directories"""
        for directory in [
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.docs_dir,
            self.log_dir
            # system_config_dir intentionally omitted - typically needs admin rights
            ]:

            directory.mkdir(parents=True, mode=0o755 ,exist_ok=True)
