"""
.. codeauthor:: Tsuyoshi Hombashi <tsuyoshi.hombashi@gmail.com>
"""

import abc
import os
from typing import ClassVar, Optional, Tuple

from ._common import normalize_platform, unprintable_ascii_chars
from ._const import DEFAULT_MIN_LEN, Platform
from ._types import PathType, PlatformType
from .error import ReservedNameError, ValidationError
from .handler import NullValueHandler, return_null_string


class BaseFile:
    _INVALID_PATH_CHARS: ClassVar[str] = "".join(unprintable_ascii_chars)
    _INVALID_FILENAME_CHARS: ClassVar[str] = _INVALID_PATH_CHARS + "/"
    _INVALID_WIN_PATH_CHARS: ClassVar[str] = _INVALID_PATH_CHARS + ':*?"<>|\t\n\r\x0b\x0c'
    _INVALID_WIN_FILENAME_CHARS: ClassVar[str] = (
        _INVALID_FILENAME_CHARS + _INVALID_WIN_PATH_CHARS + "\\"
    )

    @property
    def platform(self) -> Platform:
        return self.__platform

    @property
    def reserved_keywords(self) -> Tuple[str, ...]:
        return tuple()

    @property
    def min_len(self) -> int:
        return self._min_len

    @property
    def max_len(self) -> int:
        return self._max_len

    def __init__(
        self,
        min_len: int,
        max_len: int,
        check_reserved: bool,
        platform_max_len: Optional[int] = None,
        platform: Optional[PlatformType] = None,
    ) -> None:
        self.__platform = normalize_platform(platform)
        self._check_reserved = check_reserved

        if min_len <= 0:
            min_len = DEFAULT_MIN_LEN
        self._min_len = max(min_len, 1)

        if platform_max_len is None:
            platform_max_len = self._get_default_max_path_len()

        if max_len <= 0:
            self._max_len = platform_max_len
        else:
            self._max_len = max_len

        self._max_len = min(self._max_len, platform_max_len)
        self._validate_max_len()

    def _is_posix(self) -> bool:
        return self.platform == Platform.POSIX

    def _is_universal(self) -> bool:
        return self.platform == Platform.UNIVERSAL

    def _is_linux(self, include_universal: bool = False) -> bool:
        if include_universal:
            return self.platform in (Platform.UNIVERSAL, Platform.LINUX)

        return self.platform == Platform.LINUX

    def _is_windows(self, include_universal: bool = False) -> bool:
        if include_universal:
            return self.platform in (Platform.UNIVERSAL, Platform.WINDOWS)

        return self.platform == Platform.WINDOWS

    def _is_macos(self, include_universal: bool = False) -> bool:
        if include_universal:
            return self.platform in (Platform.UNIVERSAL, Platform.MACOS)

        return self.platform == Platform.MACOS

    def _validate_max_len(self) -> None:
        if self.max_len < 1:
            raise ValueError("max_len must be greater or equal to one")

        if self.min_len > self.max_len:
            raise ValueError("min_len must be lower than max_len")

    def _get_default_max_path_len(self) -> int:
        if self._is_linux():
            return 4096

        if self._is_windows():
            return 260

        if self._is_posix() or self._is_macos():
            return 1024

        return 260  # universal


class AbstractValidator(BaseFile, metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def validate(self, value: PathType) -> None:  # pragma: no cover
        pass

    def is_valid(self, value: PathType) -> bool:
        try:
            self.validate(value)
        except (TypeError, ValidationError):
            return False

        return True

    def _is_reserved_keyword(self, value: str) -> bool:
        return value in self.reserved_keywords


class AbstractSanitizer(BaseFile, metaclass=abc.ABCMeta):
    def __init__(
        self,
        min_len: int,
        max_len: int,
        check_reserved: bool,
        null_value_handler: Optional[NullValueHandler] = None,
        platform_max_len: Optional[int] = None,
        platform: Optional[PlatformType] = None,
        validate_after_sanitize: bool = False,
    ) -> None:
        super().__init__(
            min_len=min_len,
            max_len=max_len,
            check_reserved=check_reserved,
            platform_max_len=platform_max_len,
            platform=platform,
        )

        if null_value_handler is None:
            null_value_handler = return_null_string
        self._null_value_handler = null_value_handler

        self._validate_after_sanitize = validate_after_sanitize

    @abc.abstractmethod
    def sanitize(self, value: PathType, replacement_text: str = "") -> PathType:  # pragma: no cover
        pass


class BaseValidator(AbstractValidator):
    def _validate_reserved_keywords(self, name: str) -> None:
        if not self._check_reserved:
            return

        root_name = self.__extract_root_name(name)
        if self._is_reserved_keyword(root_name.upper()):
            raise ReservedNameError(
                f"'{root_name}' is a reserved name",
                reusable_name=False,
                reserved_name=root_name,
                platform=self.platform,
            )

    @staticmethod
    def __extract_root_name(path: str) -> str:
        return os.path.splitext(os.path.basename(path))[0]
