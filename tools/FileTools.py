"""
Collection of tools that help with file management.

@author: Marc Schulder
"""

import sys
import re
from warnings import warn
from os import path


def _tryint_(s):
    """
    Tries to turn input into an integer.
    If that fails, returns input without conversion.
    Source: http://nedbatchelder.com/blog/200712/human_sorting.html
    """
    try:
        return int(s)
    except ValueError:
        return s


def alphanum_key(s):
    """
    Turn a string into a list of string and number chunks.
    "z23a" -> ["z", 23, "a"]
    Source: http://nedbatchelder.com/blog/200712/human_sorting.html
    """
    return [_tryint_(c) for c in re.split('([0-9]+)', s)]


def getAbsPath(relativePath, workingDir=None):
    """
    Generate an absolute path for a relative path, based on a working directory.
    The main purpose of this function is to ensure that hardcoded relative paths
    always work, irrespective of where a python script has been called from.

    It turns a relative path into an absolute one, relative to a working directory.
    The default for the working directory is the directory of the main script
    that was called (as indicated by sys.argv[0])).
    This ensures that the path always refers to the same location, regardless
    from where a script was called, e.g. python foo.py or python scripts/foo.py.
    @param relativePath: A relative dir/file path.
    @param workingDir: The absolute path that relativePath is relative to.
                       If None, the path of the main python script (i.e. sys.argv[0]) is called.
    """
    if workingDir is None:
        workingDir = path.split(sys.argv[0])[0]
    abspath = path.join(workingDir, relativePath)
    realpath = path.realpath(abspath)
    return realpath


def loadDirectoryList(listFile):
    """
    Loads a file that contains references to directories (one dir per line)
    """
    directories = []
    listPath = path.dirname(listFile)
    absListPath = path.abspath(listPath)
    with open(listFile) as f:
        for line in f:
            directory = line.strip()
            if len(directory) > 0:  # Skip empty lines
                absdir = path.join(absListPath, directory)
                realdir = path.realpath(absdir)

                # Save directory
                if path.isdir(realdir):
                    directories.append(realdir)
                else:
                    warn('"{0}" is not a directory:'.format(realdir))
    return directories


def loadAirlineCallsigns(filename):
    """
    Returns a dictionary mapping airline names to their callsign, e.g. lufthansa -> DLH.
    There can be several ways to refer to the same callsign, e.g. "swiss" and "swiss_air" both map to SWR.
    """
    name2sign = {}
    with open(filename) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                try:
                    sign, name = line.split()
                except ValueError:
                    raise ValueError("Bad line in airline dictionary {} at line {}: {}".format(filename, i, line))

                if name not in name2sign:
                    name2sign[name] = sign
                else:
                    raise ValueError("Duplicate definition of airline name {}".format(name))
    return name2sign


def stripXML(text):
    """
    Remove XML annotation from a string.
    """
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text
