"""
This script processes the output from the C preprocessor and extracts all
qstr. Each qstr is transformed into a qstr definition of the form 'Q(...)'.

This script works with Python 3.x (CIRCUITPY-CHANGE: not 2.x)
"""

from __future__ import print_function

import io
import os
import re
import subprocess
import sys
import multiprocessing, multiprocessing.dummy


# CIRCUITPY-CHANGE
from html.entities import name2codepoint

# add some custom names to map characters that aren't in HTML
name2codepoint["hyphen"] = ord("-")
name2codepoint["space"] = ord(" ")
name2codepoint["squot"] = ord("'")
name2codepoint["comma"] = ord(",")
name2codepoint["dot"] = ord(".")
name2codepoint["colon"] = ord(":")
name2codepoint["semicolon"] = ord(";")
name2codepoint["slash"] = ord("/")
name2codepoint["percent"] = ord("%")
name2codepoint["hash"] = ord("#")
name2codepoint["paren_open"] = ord("(")
name2codepoint["paren_close"] = ord(")")
name2codepoint["bracket_open"] = ord("[")
name2codepoint["bracket_close"] = ord("]")
name2codepoint["brace_open"] = ord("{")
name2codepoint["brace_close"] = ord("}")
name2codepoint["star"] = ord("*")
name2codepoint["bang"] = ord("!")
name2codepoint["backslash"] = ord("\\")
name2codepoint["plus"] = ord("+")
name2codepoint["dollar"] = ord("$")
name2codepoint["equals"] = ord("=")
name2codepoint["question"] = ord("?")
name2codepoint["at_sign"] = ord("@")
name2codepoint["caret"] = ord("^")
name2codepoint["pipe"] = ord("|")
name2codepoint["tilde"] = ord("~")

# These are just vexing!
del name2codepoint["and"]
del name2codepoint["or"]
del name2codepoint["not"]

# Extract MP_QSTR_FOO macros.
_MODE_QSTR = "qstr"

# Extract MP_COMPRESSED_ROM_TEXT("") macros.  (Which come from MP_ERROR_TEXT)
_MODE_COMPRESS = "compress"

# Extract MP_REGISTER_(EXTENSIBLE_)MODULE(...) macros.
_MODE_MODULE = "module"

# Extract MP_REGISTER_ROOT_POINTER(...) macros.
_MODE_ROOT_POINTER = "root_pointer"


class PreprocessorError(Exception):
    pass


def is_c_source(fname):
    return os.path.splitext(fname)[1] in [".c"]


def is_cxx_source(fname):
    return os.path.splitext(fname)[1] in [".cc", ".cp", ".cxx", ".cpp", ".CPP", ".c++", ".C"]


def preprocess():
    if any(src in args.dependencies for src in args.changed_sources):
        sources = args.sources
    elif any(args.changed_sources):
        sources = args.changed_sources
    else:
        sources = args.sources
    csources = []
    cxxsources = []
    for source in sources:
        if is_cxx_source(source):
            cxxsources.append(source)
        elif is_c_source(source):
            csources.append(source)
    try:
        os.makedirs(os.path.dirname(args.output[0]))
    except OSError:
        pass

    def pp(flags):
        def run(files):
            try:
                return subprocess.check_output(args.pp + flags + files)
            except subprocess.CalledProcessError as er:
                raise PreprocessorError(str(er))

        return run

    try:
        cpus = multiprocessing.cpu_count()
    except NotImplementedError:
        cpus = 1
    p = multiprocessing.dummy.Pool(cpus)
    with open(args.output[0], "wb") as out_file:
        for flags, sources in (
            (args.cflags, csources),
            (args.cxxflags, cxxsources),
        ):
            batch_size = (len(sources) + cpus - 1) // cpus
            chunks = [sources[i : i + batch_size] for i in range(0, len(sources), batch_size or 1)]
            for output in p.imap(pp(flags), chunks):
                out_file.write(output)


def write_out(fname, output):
    if output:
        for m, r in [("/", "__"), ("\\", "__"), (":", "@"), ("..", "@@")]:
            fname = fname.replace(m, r)
        with open(args.output_dir + "/" + fname + "." + args.mode, "w") as f:
            f.write("\n".join(output) + "\n")


# CIRCUITPY-CHANGE: added
def qstr_unescape(qstr):
    for name in name2codepoint:
        if "__" + name + "__" in qstr:
            continue
        if "_" + name + "_" in qstr:
            qstr = qstr.replace("_" + name + "_", str(chr(name2codepoint[name])))
    return qstr


def process_file(f, output_filename=None):
    # match gcc-like output (# n "file") and msvc-like output (#line n "file")
    re_line = re.compile(r"^#(?:line)?\s+\d+\s\"([^\"]+)\"")
    if args.mode == _MODE_QSTR:
        re_match = re.compile(r"MP_QSTR_[_a-zA-Z0-9]+")
    elif args.mode == _MODE_COMPRESS:
        re_match = re.compile(r'MP_COMPRESSED_ROM_TEXT\("([^"]*)"\)')
    elif args.mode == _MODE_MODULE:
        re_match = re.compile(
            r"(?:MP_REGISTER_MODULE|MP_REGISTER_EXTENSIBLE_MODULE|MP_REGISTER_MODULE_DELEGATION)\(.*?,\s*.*?\);"
        )
    elif args.mode == _MODE_ROOT_POINTER:
        re_match = re.compile(r"MP_REGISTER_ROOT_POINTER\(.*?\);")
    # CIRCUITPY-CHANGE: added
    re_translate = re.compile(r"MP_COMPRESSED_ROM_TEXT\(\"((?:(?=(\\?))\2.)*?)\"\)")
    output = []
    last_fname = None
    for line in f:
        if line.isspace():
            continue
        m = re_line.match(line)
        if m:
            fname = m.group(1)
            if not is_c_source(fname) and not is_cxx_source(fname):
                continue
            if fname != last_fname and output_filename is None:
                write_out(last_fname, output)
                output = []
                last_fname = fname
            continue
        for match in re_match.findall(line):
            if args.mode == _MODE_QSTR:
                name = match.replace("MP_QSTR_", "")
                # CIRCUITPY-CHANGE: undo character escapes in qstrs in C code
                output.append("Q(" + qstr_unescape(name) + ")")
            elif args.mode in (_MODE_COMPRESS, _MODE_MODULE, _MODE_ROOT_POINTER):
                output.append(match)

        # CIRCUITPY-CHANGE: added
        for match in re_translate.findall(line):
            output.append('TRANSLATE("' + match[0] + '")')

    if output_filename is not None:
        with open(output_filename, "w") as f:
            f.write("\n".join(output) + "\n")
    elif last_fname:
        write_out(last_fname, output)
    return ""


def cat_together():
    import glob
    import hashlib

    hasher = hashlib.md5()
    all_lines = []
    # CIRCUITPY-CHANGE: added
    outf = open(args.output_dir + "/out", "wb")
    for fname in glob.glob(args.output_dir + "/*." + args.mode):
        with open(fname, "rb") as f:
            lines = f.readlines()
            all_lines += lines
    # CIRCUITPY-CHANGE: Check for subdirectories as well.
    for fname in glob.glob(args.output_dir + "/**/*." + args.mode):
        with open(fname, "rb") as f:
            lines = f.readlines()
            all_lines += lines
    all_lines.sort()
    all_lines = b"\n".join(all_lines)
    # CIRCUITPY-CHANGE: added
    outf.write(all_lines)
    outf.close()
    hasher.update(all_lines)
    new_hash = hasher.hexdigest()
    # print(new_hash)
    old_hash = None
    try:
        with open(args.output_file + ".hash") as f:
            old_hash = f.read()
    except IOError:
        pass
    mode_full = "QSTR"
    if args.mode == _MODE_COMPRESS:
        mode_full = "Compressed data"
    elif args.mode == _MODE_MODULE:
        mode_full = "Module registrations"
    elif args.mode == _MODE_ROOT_POINTER:
        mode_full = "Root pointer registrations"
    # CIRCUITPY-CHANGE
    if old_hash != new_hash:
        print(mode_full, "updated")
        try:
            # rename below might fail if file exists
            os.remove(args.output_file)
        except:
            pass
        os.rename(args.output_dir + "/out", args.output_file)
        with open(args.output_file + ".hash", "w") as f:
            f.write(new_hash)
    else:
        print(mode_full, "not updated")


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("usage: %s command mode input_filename output_dir output_file" % sys.argv[0])
        sys.exit(2)

    class Args:
        pass

    args = Args()
    args.command = sys.argv[1]

    if args.command == "pp":
        named_args = {
            s: []
            for s in [
                "pp",
                "output",
                "cflags",
                "cxxflags",
                "sources",
                "changed_sources",
                "dependencies",
            ]
        }

        for arg in sys.argv[1:]:
            if arg in named_args:
                current_tok = arg
            else:
                named_args[current_tok].append(arg)

        if not named_args["pp"] or len(named_args["output"]) != 1:
            print("usage: %s %s ..." % (sys.argv[0], " ... ".join(named_args)))
            sys.exit(2)

        for k, v in named_args.items():
            setattr(args, k, v)

        try:
            preprocess()
        except PreprocessorError as er:
            print(er)
            sys.exit(1)

        sys.exit(0)

    args.mode = sys.argv[2]
    args.input_filename = sys.argv[3]  # Unused for command=cat
    args.output_dir = sys.argv[4]
    args.output_file = None if len(sys.argv) == 5 else sys.argv[5]  # Unused for command=split
    if args.output_file == "_":
        args.output_file = None

    if args.mode not in (_MODE_QSTR, _MODE_COMPRESS, _MODE_MODULE, _MODE_ROOT_POINTER):
        print("error: mode %s unrecognised" % sys.argv[2])
        sys.exit(2)

    try:
        os.makedirs(args.output_dir)
    except OSError:
        pass

    if args.command == "split":
        with io.open(args.input_filename, encoding="utf-8") as infile:
            process_file(infile, args.output_file)

    if args.command == "cat":
        cat_together()
