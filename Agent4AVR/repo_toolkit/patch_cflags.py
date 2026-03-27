import os
import re
import sys


def clean_sanitizer_flags(flags: str) -> str:
    """
    Remove all sanitizer-related compiler flags from a CFLAGS/CXXFLAGS string.

    Args:
        flags (str): The original compiler flags string.
    Returns:
        str: The cleaned string with sanitizer options removed.
    """
    # Pattern matches:
    #   - -fsanitize=address,undefined
    #   - -fno-sanitize=address
    #   - -fsanitize-address-use-after-scope
    pattern = r"\s*-f(?:no-)?sanitize(?:=[^\s]+|-[^\s]+)?"

    # Remove all sanitizer flags
    cleaned = re.sub(pattern, "", flags)

    # Normalize whitespace
    cleaned = re.sub(r"\s+", " ", cleaned.strip())

    return cleaned


env_name = sys.argv[1]
env_value = os.environ.get(env_name, "")
print(clean_sanitizer_flags(env_value), end='')
