"""CppMc — a Minecraft launcher written in pure Python.

The package name is ``cppmc`` because writing a launcher for a Java game in
Python while calling it "C++mc" felt like the funniest possible choice.
"""

__all__ = ["__version__", "Launcher", "LauncherError"]

__version__ = "1.0.0"

from .core import Launcher, LauncherError  # noqa: E402  (kept at the bottom on purpose)
