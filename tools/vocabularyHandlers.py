"""
Functions for handling specific kinds of (sub)vocabularies.

@author: mschulder
"""

import os
import re
from os import path

xml_temp = '.*<{0}>(.+?)</{0}>.*'
xml_command_temp = '.*<command="{0}">(.+?)</command>.*'


def findCommandVocabularies(grammar):
    """
    List the vocabulary used for individual commands.
    Return a dict: command->vocabulary list.
    """
    open_tag = re.compile('<command=".+?">')
    close_tag = '</command>'

    # Establish searchspaces
    cmd_searchspaces = dict()
    for branches in grammar.itervalues():
        for outnode, transitions in branches.iteritems():
            if outnode != 'is_terminal':
                for transition in transitions:
                    nodeword = transition['outword'].lower()
                    if open_tag.match(nodeword):
                        searchspace = cmd_searchspaces.setdefault(nodeword, set())
                        searchspace.add(outnode)

    # Collect vocabulary of commands
    commands = dict()
    for cmd, searchspace in cmd_searchspaces.iteritems():
        commands[cmd] = _collectSubgrammarVocabulary_(searchspace, {close_tag}, grammar)
    return commands


def _collectSubgrammarVocabulary_(searchspace, stopwords, grammar):
    """
    Collect the vocabulary of a subset of the grammar.
    searchspace is a set of node IDs at which to start collecting.
    stopwords is a set of terms/tags at which to stop search.
    Return the vocabulary as a set
    """

    nodes = set(searchspace)
    in_grammar = set()
    seen_nodes = set()
    while len(nodes) > 0:
        node = nodes.pop()
        seen_nodes.add(node)
        branches = grammar[node]
        for outnode, transitions in branches.iteritems():
            if outnode != 'is_terminal' and outnode not in seen_nodes:
                for transition in transitions:
                    nodeword = transition['outword'].lower()
                    if nodeword not in stopwords:
                        in_grammar.add(nodeword)
                        nodes.add(outnode)
    return in_grammar


def findTranscriptionTagVocabulary(mainDir, tag, is_command, whitelist=None, verbose=0):
    if verbose >= 1:
        print "Checking dir", mainDir
    vocabulary = dict()
    if whitelist is None:
        whitelist = set()
    # Prepare reg exp
    if is_command:
        r = xml_command_temp.format(tag)
    else:
        r = xml_temp.format(tag)
    xml_content = re.compile(r)

    if path.isdir(mainDir):
        files = os.listdir(mainDir)
        for f in files:
            if not f.startswith('.'):
                fpath = path.join(mainDir, f)

                # Recursive directory search
                if path.isdir(fpath):
                    subvoc = findTranscriptionTagVocabulary(fpath, tag, is_command, whitelist, verbose)
                    for word, sources in subvoc.iteritems():
                        if word in vocabulary:
                            vocabulary[word] = vocabulary[word] + sources
                        else:
                            vocabulary[word] = sources

                # Check all xml annotations for tag content
                elif path.isfile(fpath) and f.endswith('.tra'):
                    with open(fpath) as reader:
                        text = reader.read()
                        match = xml_content.match(text)
                        if match is not None:
                            words = match.group(1).strip().split()
                            for i, word in enumerate(words):
                                if word not in whitelist:
                                    if verbose >= 1 and (i == 0 or i + 1 == len(words)):
                                        print 'Unknown border word "{0} in {1}"'.format(word, fpath)

                                    if word in vocabulary:
                                        vocabulary[word].append(fpath)
                                    else:
                                        vocabulary[word] = [fpath]
    return vocabulary
