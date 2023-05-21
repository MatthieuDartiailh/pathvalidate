"""
.. codeauthor:: Tsuyoshi Hombashi <tsuyoshi.hombashi@gmail.com>
"""

import ntpath
import os.path
import posixpath
import re
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

from ._base import AbstractSanitizer, BaseFile, BaseValidator
from ._common import findall_to_str, to_str, validate_pathtype
from ._const import _NTFS_RESERVED_FILE_NAMES, DEFAULT_MIN_LEN, INVALID_CHAR_ERR_MSG_TMPL, Platform
from ._filename import FileNameSanitizer, FileNameValidator
from ._types import PathType, PlatformType
from .error import (
    ErrorReason,
    InvalidCharError,
    InvalidLengthError,
    ReservedNameError,
    ValidationError,
)
from .handler import NullValueHandler


_RE_INVALID_PATH = re.compile(f"[{re.escape(BaseFile._INVALID_PATH_CHARS):s}]", re.UNICODE)
_RE_INVALID_WIN_PATH = re.compile(f"[{re.escape(BaseFile._INVALID_WIN_PATH_CHARS):s}]", re.UNICODE)


class FilePathSanitizer(AbstractSanitizer):
    def __init__(
        self,
        min_len: int = DEFAULT_MIN_LEN,
        max_len: int = -1,
        platform: Optional[PlatformType] = None,
        check_reserved: bool = True,
        null_value_handler: Optional[NullValueHandler] = None,
        normalize: bool = True,
        validate_after_sanitize: bool = False,
    ) -> None:
        super().__init__(
            min_len=min_len,
            max_len=max_len,
            check_reserved=check_reserved,
            null_value_handler=null_value_handler,
            platform=platform,
            validate_after_sanitize=validate_after_sanitize,
        )

        self._sanitize_regexp = self._get_sanitize_regexp()
        self.__fpath_validator = FilePathValidator(
            min_len=self.min_len,
            max_len=self.max_len,
            check_reserved=check_reserved,
            platform=self.platform,
        )
        self.__fname_sanitizer = FileNameSanitizer(
            min_len=self.min_len,
            max_len=self.max_len,
            check_reserved=check_reserved,
            platform=self.platform,
            validate_after_sanitize=validate_after_sanitize,
        )
        self.__normalize = normalize

        if self._is_windows(include_universal=True):
            self.__split_drive = ntpath.splitdrive
        else:
            self.__split_drive = posixpath.splitdrive

    def sanitize(self, value: PathType, replacement_text: str = "") -> PathType:
        try:
            validate_pathtype(value, allow_whitespaces=not self._is_windows(include_universal=True))
        except ValidationError as e:
            if e.reason == ErrorReason.NULL_NAME:
                if isinstance(value, Path):
                    raise

                return self._null_value_handler(e)
            raise

        self.__fpath_validator.validate_abspath(value)

        unicode_filepath = to_str(value)

        drive, unicode_filepath = self.__split_drive(unicode_filepath)
        unicode_filepath = self._sanitize_regexp.sub(replacement_text, unicode_filepath)
        if self.__normalize and unicode_filepath:
            unicode_filepath = os.path.normpath(unicode_filepath)
        sanitized_path = unicode_filepath

        sanitized_entries: List[str] = []
        if drive:
            sanitized_entries.append(drive)
        for entry in sanitized_path.replace("\\", "/").split("/"):
            if entry in _NTFS_RESERVED_FILE_NAMES:
                sanitized_entries.append(f"{entry}_")
                continue

            sanitized_entry = str(
                self.__fname_sanitizer.sanitize(entry, replacement_text=replacement_text)
            )
            if not sanitized_entry:
                if not sanitized_entries:
                    sanitized_entries.append("")
                continue

            sanitized_entries.append(sanitized_entry)

        sanitized_path = self.__get_path_separator().join(sanitized_entries)
        try:
            self.__fpath_validator.validate(sanitized_path)
        except ValidationError as e:
            if e.reason == ErrorReason.NULL_NAME:
                sanitized_path = self._null_value_handler(e)
            else:
                raise

        if self._validate_after_sanitize:
            self.__fpath_validator.validate(sanitized_path)

        if isinstance(value, Path):
            return Path(sanitized_path)

        return sanitized_path

    def _get_sanitize_regexp(self) -> Pattern[str]:
        if self._is_windows(include_universal=True):
            return _RE_INVALID_WIN_PATH

        return _RE_INVALID_PATH

    def __get_path_separator(self) -> str:
        if self._is_windows():
            return "\\"

        return "/"


class FilePathValidator(BaseValidator):
    _RE_NTFS_RESERVED = re.compile(
        "|".join(f"^/{re.escape(pattern)}$" for pattern in _NTFS_RESERVED_FILE_NAMES),
        re.IGNORECASE,
    )
    _MACOS_RESERVED_FILE_PATHS = ("/", ":")

    @property
    def reserved_keywords(self) -> Tuple[str, ...]:
        common_keywords = super().reserved_keywords

        if any([self._is_universal(), self._is_posix(), self._is_macos()]):
            return common_keywords + self._MACOS_RESERVED_FILE_PATHS

        if self._is_linux():
            return common_keywords + ("/",)

        return common_keywords

    def __init__(
        self,
        min_len: int = DEFAULT_MIN_LEN,
        max_len: int = -1,
        platform: Optional[PlatformType] = None,
        check_reserved: bool = True,
    ) -> None:
        super().__init__(
            min_len=min_len,
            max_len=max_len,
            check_reserved=check_reserved,
            platform=platform,
        )

        self.__fname_validator = FileNameValidator(
            min_len=min_len, max_len=max_len, check_reserved=check_reserved, platform=platform
        )

        if self._is_windows(include_universal=True):
            self.__split_drive = ntpath.splitdrive
        else:
            self.__split_drive = posixpath.splitdrive

    def validate(self, value: PathType) -> None:
        validate_pathtype(value, allow_whitespaces=not self._is_windows(include_universal=True))
        self.validate_abspath(value)

        _drive, tail = self.__split_drive(value)
        if not tail:
            return

        unicode_filepath = to_str(tail)
        value_len = len(unicode_filepath)

        if value_len > self.max_len:
            raise InvalidLengthError(
                f"file path is too long: expected<={self.max_len:d}, actual={value_len:d}"
            )
        if value_len < self.min_len:
            raise InvalidLengthError(
                "file path is too short: expected>={:d}, actual={:d}".format(
                    self.min_len, value_len
                )
            )

        self._validate_reserved_keywords(unicode_filepath)
        unicode_filepath = unicode_filepath.replace("\\", "/")
        for entry in unicode_filepath.split("/"):
            if not entry or entry in (".", ".."):
                continue

            self.__fname_validator._validate_reserved_keywords(entry)

        if self._is_windows(include_universal=True):
            self.__validate_win_filepath(unicode_filepath)
        else:
            self.__validate_unix_filepath(unicode_filepath)

    def validate_abspath(self, value: PathType) -> None:
        is_posix_abs = posixpath.isabs(value)
        is_nt_abs = ntpath.isabs(value)
        err_object = ValidationError(
            description=(
                "an invalid absolute file path ({}) for the platform ({}).".format(
                    value, self.platform.value
                )
                + " to avoid the error, specify an appropriate platform corresponding to"
                + " the path format or 'auto'."
            ),
            platform=self.platform,
            reason=ErrorReason.MALFORMED_ABS_PATH,
        )

        if any([self._is_windows() and is_nt_abs, self._is_linux() and is_posix_abs]):
            return

        if self._is_universal() and any([is_posix_abs, is_nt_abs]):
            ValidationError(
                description=(
                    ("POSIX style" if is_posix_abs else "NT style")
                    + " absolute file path found. expected a platform-independent file path."
                ),
                platform=self.platform,
                reason=ErrorReason.MALFORMED_ABS_PATH,
            )

        if self._is_windows(include_universal=True) and is_posix_abs:
            raise err_object

        drive, _tail = ntpath.splitdrive(value)
        if not self._is_windows() and drive and is_nt_abs:
            raise err_object

    def __validate_unix_filepath(self, unicode_filepath: str) -> None:
        match = _RE_INVALID_PATH.findall(unicode_filepath)
        if match:
            raise InvalidCharError(
                INVALID_CHAR_ERR_MSG_TMPL.format(
                    invalid=findall_to_str(match), value=repr(unicode_filepath)
                )
            )

    def __validate_win_filepath(self, unicode_filepath: str) -> None:
        match = _RE_INVALID_WIN_PATH.findall(unicode_filepath)
        if match:
            raise InvalidCharError(
                INVALID_CHAR_ERR_MSG_TMPL.format(
                    invalid=findall_to_str(match), value=repr(unicode_filepath)
                ),
                platform=Platform.WINDOWS,
            )

        _drive, value = self.__split_drive(unicode_filepath)
        if value:
            match_reserved = self._RE_NTFS_RESERVED.search(value)
            if match_reserved:
                reserved_name = match_reserved.group()
                raise ReservedNameError(
                    f"'{reserved_name}' is a reserved name",
                    reusable_name=False,
                    reserved_name=reserved_name,
                    platform=self.platform,
                )


def validate_filepath(
    file_path: PathType,
    platform: Optional[PlatformType] = None,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: Optional[int] = None,
    check_reserved: bool = True,
) -> None:
    """Verifying whether the ``file_path`` is a valid file path or not.

    Args:
        file_path:
            File path to validate.
        platform:
            Target platform name of the file path.

            .. include:: platform.txt
        min_len:
            Minimum length of the ``file_path``. The value must be greater or equal to one.
            Defaults to ``1``.
        max_len:
            Maximum length of the ``file_path`` length. If the value is |None| or minus,
            automatically determined by the ``platform``:

                - ``Linux``: 4096
                - ``macOS``: 1024
                - ``Windows``: 260
                - ``universal``: 260
        check_reserved:
            If |True|, check reserved names of the ``platform``.

    Raises:
        ValidationError (ErrorReason.INVALID_CHARACTER):
            If the ``file_path`` includes invalid char(s):
            |invalid_file_path_chars|.
            The following characters are also invalid for Windows platforms:
            |invalid_win_file_path_chars|
        ValidationError (ErrorReason.INVALID_LENGTH):
            If the ``file_path`` is longer than ``max_len`` characters.
        ValidationError:
            If ``file_path`` include invalid values.

    Example:
        :ref:`example-validate-file-path`

    See Also:
        `Naming Files, Paths, and Namespaces - Win32 apps | Microsoft Docs
        <https://docs.microsoft.com/en-us/windows/win32/fileio/naming-a-file>`__
    """

    FilePathValidator(
        platform=platform,
        min_len=min_len,
        max_len=-1 if max_len is None else max_len,
        check_reserved=check_reserved,
    ).validate(file_path)


def is_valid_filepath(
    file_path: PathType,
    platform: Optional[PlatformType] = None,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: Optional[int] = None,
    check_reserved: bool = True,
) -> bool:
    """Check whether the ``file_path`` is a valid name or not.

    Args:
        file_path:
            A filepath to be checked.

    Example:
        :ref:`example-is-valid-filepath`

    See Also:
        :py:func:`.validate_filepath()`
    """

    return FilePathValidator(
        platform=platform,
        min_len=min_len,
        max_len=-1 if max_len is None else max_len,
        check_reserved=check_reserved,
    ).is_valid(file_path)


def sanitize_filepath(
    file_path: PathType,
    replacement_text: str = "",
    platform: Optional[PlatformType] = None,
    max_len: Optional[int] = None,
    check_reserved: bool = True,
    null_value_handler: Optional[NullValueHandler] = None,
    normalize: bool = True,
    validate_after_sanitize: bool = False,
) -> PathType:
    """Make a valid file path from a string.

    To make a valid file path, the function does the following:

        - replace invalid characters for a file path within the ``file_path``
          with the ``replacement_text``. Invalid characters are as follows:

            - unprintable characters
            - |invalid_file_path_chars|
            - for Windows (or universal) only: |invalid_win_file_path_chars|

        - Append underscore (``"_"``) at the tail of the name if sanitized name
          is one of the reserved names by operating systems
          (only when ``check_reserved`` is |True|).

    Args:
        file_path:
            File path to sanitize.
        replacement_text:
            Replacement text for invalid characters.
            Defaults to ``""``.
        platform:
            Target platform name of the file path.

            .. include:: platform.txt
        max_len:
            Maximum length of the ``file_path`` length. Truncate the name if the ``file_path``
            length exceedd this value. If the value is |None| or minus,
            ``max_len`` will automatically determined by the ``platform``:

                - ``Linux``: 4096
                - ``macOS``: 1024
                - ``Windows``: 260
                - ``universal``: 260
        check_reserved:
            If |True|, sanitize reserved names of the ``platform``.
        null_value_handler:
            Function called when a value after sanitization is an empty string.
            Defaults to ``pathvalidate.handler.return_null_string()`` that just return ``""``.
        normalize:
            If |True|, normalize the the file path.
        validate_after_sanitize:
            Execute validation after sanitization to the file path.

    Returns:
        Same type as the argument (str or PathLike object):
            Sanitized filepath.

    Raises:
        ValueError:
            If the ``file_path`` is an invalid file path.

    Example:
        :ref:`example-sanitize-file-path`
    """

    return FilePathSanitizer(
        platform=platform,
        max_len=-1 if max_len is None else max_len,
        check_reserved=check_reserved,
        normalize=normalize,
        null_value_handler=null_value_handler,
        validate_after_sanitize=validate_after_sanitize,
    ).sanitize(file_path, replacement_text)
