"""Indentation for explicitly designated answer regions."""
import re


def solution_indent(prefix, header):
    indent = re.match(r'[ \t]*', header).group()
    # Some instructor templates align the comment with `for`, although the
    # missing answer must form its body. Comments do not establish Python scope.
    for line in reversed(prefix.splitlines()):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        previous = re.match(r'[ \t]*', line).group()
        if line.rstrip().endswith(':') and len(previous.expandtabs(8)) >= len(indent.expandtabs(8)):
            return previous + ('\t' if '\t' in previous else '    ')
        break
    return indent
