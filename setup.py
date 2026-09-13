"""Setuptools hooks that keep macOS AppleDouble metadata out of distributions."""

from __future__ import annotations

import os
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py
from setuptools.command.egg_info import egg_info


def _is_appledouble(path: str) -> bool:
    return any(part.startswith("._") for part in Path(path).parts)


class CleanBuildPy(build_py):
    """Exclude sidecar files that otherwise look like importable Python modules."""

    def find_package_modules(self, package: str, package_dir: str):
        return [
            module
            for module in super().find_package_modules(package, package_dir)
            if not _is_appledouble(module[2])
        ]


class CleanEggInfo(egg_info):
    """Exclude AppleDouble sidecars from the source manifest."""

    def find_sources(self) -> None:
        super().find_sources()
        self.filelist.files = [
            path for path in self.filelist.files if not _is_appledouble(path)
        ]


class CleanBdistWheel(bdist_wheel):
    """Remove volume-generated sidecars from wheel staging before archiving."""

    def write_wheelfile(self, wheelfile_base: str) -> None:
        super().write_wheelfile(wheelfile_base)
        for root, directories, files in os.walk(self.bdist_dir, topdown=False):
            for name in files:
                if name.startswith("._"):
                    Path(root, name).unlink()
            for name in directories:
                if name.startswith("._"):
                    Path(root, name).rmdir()


setup(
    cmdclass={
        "bdist_wheel": CleanBdistWheel,
        "build_py": CleanBuildPy,
        "egg_info": CleanEggInfo,
    }
)
