"""
Conversion of strings and text files from raw text to XML-enriched versions, 
based on the grammar of the currently compiled recognition network.

Use the various convertXYZ methods of the Text2XMLConverter class.

@author: Marc Schulder
"""

import sys
import os
import re
from os import path
from signal import signal, alarm, SIGALRM
from warnings import warn
from functools import partial
from operator import itemgetter
from .vocabularyHandlers import findCommandVocabularies
from .FileTools import loadAirlineCallsigns


CONF_SEPARATOR = ':'


class TimeoutError(Exception):
    pass


def getAbsolutePath():
    """
    Ensure that script can be executed from anywhere (e.g. via python tools/GenerateConcept.py)
    """
    scriptpath = path.dirname(sys.argv[0])
    return path.abspath(scriptpath)


def loadGrammar(filename):
    grammar = {}
    with open(filename) as f:
        for line in f:
            elems = line.strip().split('\t')
            in_node = int(elems[0])

            if len(elems) <= 1:
                grammar[in_node] = {'is_terminal': True}
            else:
                out_node = int(elems[1])
                # in_word = elems[2]
                # out_word = elems[3]
                if in_node not in grammar:
                    grammar[in_node] = {'is_terminal': False}
                if out_node not in grammar[in_node]:
                    grammar[in_node][out_node] = list()

                token = {'is_terminal': False,
                         'inword': elems[2],
                         'outword': elems[3]}
                if len(elems) >= 5:
                    token['weight'] = float(elems[4])

                grammar[in_node][out_node].append(token)
    return grammar


class SkipFST:
    def __init__(self, airlineFile):
        self.is_xml = re.compile("<.+?>")
        self.tagRE = re.compile("</?(.+?)>")
        self.default_callsign_whitelist = {'aeh', 'ah', 'correction', 'ne'}
        self.letters = {'alpha', 'bravo', 'charly', 'delta', 'echo', 'fox', 'foxtrot', 'golf', 'hotel', 'india',
                        'juliett', 'kilo', 'lima', 'mike', 'november', 'oscar', 'papa', 'quebec', 'romeo', 'sierra',
                        'tango', 'uniform', 'victor', 'whisky', 'xray', 'yankee', 'zoulou'}
        self.single_digits = {'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'}
        self.noise_markers = {'_sil_', '_spn_', '_nsn_'}

        absolutepath = getAbsolutePath()
        airlineFile = path.join(absolutepath, airlineFile)
        airlineDict = loadAirlineCallsigns(airlineFile)
        self.airlines = set(airlineDict.keys())

    def transduceTimerHandler(self, signum, frame, text):
        raise TimeoutError("The following parse took too long:", text)

    @staticmethod
    def _lower_(string):
        return string.lower()

    def getBestParse(self, sentence, grammar, cmdVocabularies=None, callsign_whitelist=None, wxLib=None, wxGUI=None,
                     timeout=0, allowSkip=True, vocabulary=None, ignoreFluffWords=False, airlineCorrectionCutoff=20):
        parses = self.transduce(sentence, grammar, False, cmdVocabularies, callsign_whitelist, wxLib=wxLib,
                                wxGUI=wxGUI, timeout=timeout, vocabulary=vocabulary,
                                ignoreFluffWords=ignoreFluffWords, airlineCorrectionCutoff=airlineCorrectionCutoff)
        if allowSkip and len(parses) == 0:
            parses = self.transduce(sentence, grammar, True, cmdVocabularies, callsign_whitelist, wxLib=wxLib,
                                    wxGUI=wxGUI, timeout=timeout, vocabulary=vocabulary,
                                    ignoreFluffWords=ignoreFluffWords, airlineCorrectionCutoff=airlineCorrectionCutoff)
        elif wxLib is not None and wxGUI is not None:  # If parse w\o skipping worked, set progress gauge in GUI to full
            wxLib.CallAfter(wxGUI.updateXMLGauge, 100)

        if len(parses) == 0:
            return None
        else:
            return ' '.join(parses[0][0])

    def transduce(self, sentence, grammar, allow_skip=True, cmdVocabularies=None, callsign_whitelist=None,
                  wxLib=None, wxGUI=None, timeout=0, vocabulary=None, ignoreFluffWords=False,
                  airlineCorrectionCutoff=20):
        """
        Transduces a Kaldi grammar to add XML to a sentence.
        Can skip words in the input sentence.
        Skipping words in the callsign is limited to words defined by callsign_whitelist and words surrounded by
        underscores, e.g._word_.

        :param sentence:
        :param grammar:
        :param allow_skip: If True (default), words can be skipped
        :param cmdVocabularies:
        :param callsign_whitelist: List of words that can be skipped while parsing a callsign. If no list is provided,
                                   default_callsign_whitelist is used.
        :param wxLib:
        :param wxGUI:
        :param timeout: Number of seconds after which parsing will be aborted and the currently best parse returned.
                        If set 0 (default), no timeout will occur.
        :param vocabulary:
        :param ignoreFluffWords:
        :param airlineCorrectionCutoff:
        :return: A sorted list of (parse,cost)-tuples, where parse is a string representing a potential parse of the
                 sentence and cost is itself a tuple (tag-cost,skip-cost).
                 Beware that there might be parses with identical costs.
        """
        if len(sentence) == 0:
            return []

        # If no grammar was provided, transduction is not possible
        if not grammar:
            return []

        # Prepare auxiliary information
        originalSentence = ' '.join(sentence)
        sentence = list(map(self._lower_, sentence))

        if callsign_whitelist is None:
            callsign_whitelist = self.default_callsign_whitelist
        irrelevant_commands = set()
        if cmdVocabularies is not None:
            words = self._removeConfidences_(sentence)
            filtered_sentence = set(words)
            filtered_sentence.difference_update(self.single_digits)
            filtered_sentence.difference_update(self.letters)
            for cmd, vocab in cmdVocabularies.items():
                if vocab.isdisjoint(filtered_sentence):
                    irrelevant_commands.add(cmd)

        # Prepare arguments
        useGUI = wxLib is not None and wxGUI is not None
        isAborted = False
        start_node = 0
        lookup_count = 0

        # Remove unnecessary "fluff" words from sentence
        if allow_skip and ignoreFluffWords:
            verboseFluff = False  # Debug switch
            sentence = self.removeOOGWords(sentence, vocabulary, verbose=verboseFluff)

        # Fix airline corrections by removing start of sentence in case of more than one airline name showing up.
        # Only happens if the cutoff is not later than airlineCorrectionCutoff
        if allow_skip and airlineCorrectionCutoff is not None:
            airlines = self.airlines.difference(self.letters)
            airlineIndices = set()
            sentence_no_cofid = [x.split(':', 1)[0] for x in sentence]
            for airline in airlines:
                if airline in sentence_no_cofid:
                    reverseIndex = sentence_no_cofid[::-1].index(airline)
                    index = len(sentence) - reverseIndex
                    airlineIndices.add(index)
            if len(airlineIndices):
                cutoff = max(airlineIndices) - 1
                if cutoff <= airlineCorrectionCutoff:
                    sentence = sentence[cutoff:]

        # Format: {remaining words:
        #             (output, remaining_sentence, skips, tagcost, open_tags, current_node, skipped_words, in_callsign)}
        parses = {0: [([], sentence, 0, 0, [], start_node, [], False)]}
        complete_parses = []
        seen_parses = dict()

        # Start up timeout alarm
        # The annotator GUI doesn't need timeout, because users can abort manually.
        # Also, the GUI puts the converter in a thread, but signal can only be called be the main thread. 
        if not useGUI:
            signal(SIGALRM, partial(self.transduceTimerHandler, text=originalSentence))
            alarm(timeout)

        bestDistance = sys.maxsize

        # Start parsing
        i = 0
        slen = len(sentence)
        try:
            # Iterate over all words. i is index of word in sentence.
            while i <= slen and not isAborted:
                # print '{0} of {1} remaining: {2}'.format(slen-i,slen,' '.join(sentence[i:]))
                parse_level = parses[i]
                next_parse = parses.setdefault(i + 1, [])

                if useGUI and allow_skip:
                    sorted_parses = self._sortParses_(complete_parses, parses[i])
                    wxLib.CallAfter(wxGUI.XMLCorr.SetValue, ' '.join(sorted_parses[0][0]))

                j = 0
                # Iterate over all possible transitions for word i.
                # If transition reads in a word or is skipped
                #    the parse is used as starting point for the next parse level (i.e. next word)
                # If transition is for XML, the tag is added to the output 
                #    and the parse is added to list of parses that we are iterating 
                #    through right now
                while len(parse_level) > 0 and not isAborted:  # Each iteration tries
                    (output, remaining_sentence, skips, tagcost, open_tags, current_node, skipped_words,
                     in_callsign) = parse_level.pop(0)

                    if skips > bestDistance:
                        continue

                    # remove parses that have already been attempted
                    # (i.e. at this node and after skipping these words,
                    #       we already reached this output)
                    outstr = ' '.join(output)
                    skipstr = ' '.join(skipped_words)
                    if current_node not in seen_parses:
                        seen_parses[current_node] = {skipstr: [outstr]}
                    elif skipstr not in seen_parses[current_node]:
                        seen_parses[current_node][skipstr] = [outstr]
                    elif outstr not in seen_parses[current_node][skipstr]:
                        seen_parses[current_node][skipstr].append(outstr)
                    else:
                        continue

                    lookup_count += 1
                    branches = grammar[current_node]

                    if branches['is_terminal']:
                        remaining_distance = len(remaining_sentence)
                        bestDistance = skips + remaining_distance
                        if allow_skip:
                            # Add closing tags
                            for tag in open_tags[::-1]:
                                output.append(self._getClosingTag_(tag))

                            complete_parses.append((
                                                   output, '', skips + remaining_distance, tagcost + remaining_distance,
                                                   list(), -1, skipped_words + remaining_sentence, False))
                        elif remaining_distance == 0:
                            complete_parses.append((output, '', skips, tagcost, list(), -1, skipped_words, False))
                    else:
                        considered_word = False
                        for outnode, transitions in branches.items():
                            if outnode != 'is_terminal':
                                for transition in transitions:
                                    nodeword = transition['outword'].lower()
                                    # Epsilon transition
                                    isTag = self.tagRE.match(nodeword)
                                    if nodeword == '<eps>':
                                        parse_level.append((output, remaining_sentence, skips, tagcost, open_tags,
                                                            outnode, skipped_words, in_callsign))
                                    # XML tag transition
                                    elif isTag:
                                        nodeword_tag = isTag.group(1)
                                        # Closing XML tag
                                        if nodeword.startswith('</'):
                                            remaining_open_tags, closed_tags = self._closeTags_(nodeword_tag, open_tags)
                                            parse_level.append((output + closed_tags, remaining_sentence, skips,
                                                                tagcost - len(closed_tags), remaining_open_tags,
                                                                outnode, skipped_words,
                                                                (nodeword != '</callsign>' and in_callsign)))
                                        # Opening XML tag
                                        elif nodeword not in irrelevant_commands:
                                            parse_level.append((output + [nodeword], remaining_sentence, skips,
                                                                tagcost + 1, open_tags + [nodeword_tag], outnode,
                                                                skipped_words,
                                                                (nodeword == '<callsign>' or in_callsign)))
                                    # Regular word (if any are left)
                                    elif len(remaining_sentence) > 0:
                                        considered_word = True
                                        # Word parseable
                                        token = remaining_sentence[0].lower()
                                        observation = self._removeConfidence_(token)
                                        if nodeword == observation:
                                            next_parse.append((output + [token], remaining_sentence[1:], skips, tagcost,
                                                               open_tags, outnode, skipped_words, in_callsign))
                        # Skip words
                        if len(remaining_sentence) > 0:
                            token = remaining_sentence[0].lower()
                            observation = self._removeConfidence_(token)
                            isNoise = observation in self.noise_markers
                            obs_marked_oog = not isNoise and (observation[0] == '_' and observation[-1] == '_')
                            isAirline = observation in self.airlines and observation not in self.letters
                            # Allow skipping only if this node allowed reading in words (considered_word)
                            # and when we're either NOT currently reading in the callsign
                            # OR if the next word is either whitelisted for callsign skipping
                            # or its marked as definitely out of grammar by surrounding underscores.
                            # Even when allow_skip is false, skipping is still allowed when dealing with noise markers.
                            if (allow_skip or isNoise) and considered_word and not isAirline and (
                                    not in_callsign or observation in callsign_whitelist or obs_marked_oog):
                                next_parse = parses.setdefault(i + 1, [])
                                if isNoise:
                                    updated_skips = skips  # Don't penalise for skipping noise markers
                                else:
                                    updated_skips = skips + 1
                                next_parse.append((output, remaining_sentence[1:], updated_skips, tagcost, open_tags,
                                                   current_node, skipped_words + [token], in_callsign))
                        # GUI Feedback
                        if useGUI and wxGUI.xmlGenerationIsAborted:
                            isAborted = True
                            i -= 1  # Counterbalance the last increment step
                    j += 1
                i += 1
                if useGUI and allow_skip:
                    wxLib.CallAfter(wxGUI.updateXMLGauge, i * 100 / slen + 1)
        except TimeoutError:
            isAborted = True
        # Disable the alarm
        if not useGUI:
            alarm(0)

        # Process incomplete parses
        if isAborted:
            sorted_parses = self._sortParses_(complete_parses, parses[i])
            if useGUI and allow_skip:
                wxLib.CallAfter(wxGUI.updateXMLGauge, (i + 1) * 100 / slen + 1)
        # Process completed parses
        else:
            sorted_parses = self._sortParses_(complete_parses)
            if useGUI and allow_skip:
                wxLib.CallAfter(wxGUI.updateXMLGauge, 100)

        return sorted_parses

    @staticmethod
    def _removeConfidence_(token):
        if CONF_SEPARATOR in token:
            word, _ = token.rsplit(CONF_SEPARATOR, 1)
        else:
            word = token
        return word

    @staticmethod
    def _removeConfidences_(sentence):
        words = list()
        for token in sentence:
            word = SkipFST._removeConfidence_(token)
            words.append(word)
        return words

    @staticmethod
    def _splitToken_(token):
        if CONF_SEPARATOR in token:
            word, confidence = token.rsplit(CONF_SEPARATOR, 1)
        else:
            word = token
            confidence = None
        return word, confidence

    @staticmethod
    def _splitTokens_(sentence):
        tokens = list()
        for token in sentence:
            word, confidence = SkipFST._splitToken_(token)
            tokens.append((word, confidence))
        return tokens

    @staticmethod
    def _sortParses_(complete_parses, incomplete_parses=None):
        # Sort completed parses 
        presort_complete = sorted(complete_parses, key=itemgetter(3))  # Secondary sort by tagcost
        sorted_complete = sorted(presort_complete, key=itemgetter(2))  # Primary sort by skips
        if len(complete_parses) > 0:
            cost_complete = sorted_complete[0][3]  # Complete cost is skips
        else:
            cost_complete = sys.maxsize

        # Sort incomplete parses
        sorted_incomplete = []
        if incomplete_parses is not None and len(incomplete_parses) > 0:
            # Force complete the incomplete parses
            force_closed_parses = list()
            for parse in incomplete_parses:
                output, remaining_sentence, skips, tagcost, open_tags, current_node, skipped_words, in_callsign = parse
                output = output[:]
                for tag in open_tags[::-1]:
                    if tag == 's':
                        output.extend(remaining_sentence)
                        skips += len(remaining_sentence)
                        remaining_sentence = list()
                    output.append(SkipFST._getClosingTag_(tag))
                closed_parse = output, remaining_sentence, skips, 0, list(), current_node, skipped_words, in_callsign
                force_closed_parses.append(closed_parse)
            # Sort parses
            sorted_incomplete = sorted(force_closed_parses, key=itemgetter(2))  # Primary sort by skips
            cost_incomplete = sorted_incomplete[0][3] + len(
                sorted_incomplete[0][1])  # Incomplete cost is skips plus remaining unparsed words
        else:
            cost_incomplete = sys.maxsize

        # Pick better parse set
        if cost_complete <= cost_incomplete:
            return sorted_complete
        else:
            return sorted_incomplete

    @staticmethod
    def _makeParsesUnique_(parses):
        # Reduce to unique parses
        unique_parses = dict()
        for parse in parses:
            output = parse[0]
            skips = parse[2]
            tagcost = parse[3]
            pout = ' '.join(output)
            if pout not in unique_parses:
                unique_parses[pout] = (skips, tagcost)
            else:
                # If parse was encountered previously, choose version with lowest cost
                oldskips, oldtag = unique_parses[pout]
                if skips < oldskips or (skips == oldskips and tagcost < oldtag):
                    unique_parses[pout] = (skips, tagcost)
        return unique_parses

    @staticmethod
    def _getClosingTag_(tagName):
        """
        Given a tag's value (i.e. the tag without surrounding < and >), return the closing tag to an opening tag.
        """
        if tagName.startswith('command='):
            tagName = 'command'  # The ending tag for commands is just </command>
        return '</{0}>'.format(tagName)

    @staticmethod
    def _closeTags_(nodeword_tag, open_tags):
        """
        Takes the current nodeword's tag and checks which tag in the list it is closing.
        If there are other tags that need to be closed before to avoid crossing tags,
        they are moved from open tags to closed tags too.
        Returns two lists: The remaining open tags and the closed tags that should be appended to the parsed sentence.
        """
        remaining_open_tags = open_tags[:]  # Make a copy of the list
        closed_tags = list()
        while len(remaining_open_tags) > 0:
            tag = remaining_open_tags.pop()
            if tag.startswith('command='):
                tag = 'command'
            closed_tag = SkipFST._getClosingTag_(tag)
            closed_tags.append(closed_tag)  # Every tag up until the nodeword tag must be closed
            if nodeword_tag == tag:  # We've found the nodeword tag
                break
        return remaining_open_tags, closed_tags

    def addMissingWords(self, sentence, base_xml):
        sentence = list(map(str.lower, sentence))
        xmlSentence = base_xml.strip().split()
        seenWords, remainingWords, xmlSentence = self._addMissingWords(sentence, xmlSentence)
        return ' '.join(seenWords)

    def _addMissingWords(self, remainingWords, xmlSentence, depth=0):
        if len(xmlSentence) == 0:
            return remainingWords, list(), xmlSentence
        seen = list()
        remainingWords = remainingWords[:]
        i = -1
        for i, xmlWord in enumerate(xmlSentence):
            if self.tagRE.match(xmlWord):  # is a tag
                if xmlWord.startswith('</'):  # is a closing tag
                    if xmlWord == '</s>':
                        seen.extend(remainingWords)
                        remainingWords = list()
                    seen.append(xmlWord)
                    return seen, remainingWords, xmlSentence[i + 1:]
                else:  # is an opening tag
                    remainingXMLSentence = xmlSentence[i + 1:]
                    seenInside, remainingWordsInside, xmlSentence[i + 1:] = self._addMissingWords(remainingWords,
                                                                                                  remainingXMLSentence,
                                                                                                  depth=depth + 1)
                    if xmlWord != '<s>':
                        beforeTag = list()
                        for j, insideWord in enumerate(seenInside):
                            nextWord = remainingXMLSentence[0]
                            if insideWord == nextWord or self.tagRE.match(insideWord):
                                seenInside = seenInside[j:]
                                break
                            else:
                                beforeTag.append(insideWord)
                        seen.extend(beforeTag)
                    seen.append(xmlWord)
                    seen.extend(seenInside)
                    remainingWords = remainingWordsInside
            else:  # is a word
                isMatch = False
                while not isMatch and len(remainingWords) > 0:
                    remainingWord = remainingWords.pop(0)
                    if xmlWord == remainingWord:
                        seen.append(xmlWord)
                        isMatch = True
                    else:
                        seen.append(remainingWord)
        return seen, remainingWords, xmlSentence[i + 1:]

    @staticmethod
    def removeOOGWords(sentence, vocabulary, verbose=False):
        defluffedSentence = list()
        for token in sentence:
            observation = SkipFST._removeConfidence_(token.lower())
            if observation.upper() in vocabulary:
                defluffedSentence.append(token)
        if verbose:
            if len(sentence) == len(defluffedSentence):
                print("No OOG words in sentence:", ' '.join(sentence))
            else:
                print("original sentence: ", ' '.join(sentence))
                print("Defluffed sentence:", ' '.join(defluffedSentence))
        return defluffedSentence


class Text2XMLConverter:
    """
    Conversion of strings and text files from raw text to XML-enriched versions, 
    based on the grammar of the currently compiled recognition network.
    Use the various convertXYZ methods of the class.
    """

    def __init__(self, grammarFile, airlineFile):
        # Prepare session data
        self.grammar = None
        self.cmdVocabs = None
        self.vocab = None
        self.grammar_name = None
        self.has_grammar = False
        self.prepareSession(grammarFile)
        self.skipFST = SkipFST(airlineFile)

    def prepareSession(self, grammarFile):
        """
        Prepares data that will be consistent throughout the session.
        Also ensures the directory for temporary files exists
        """
        # Generate modified grammar for XML transduction
        try:
            self.grammar = loadGrammar(grammarFile)
            self.has_grammar = True
        except IOError:
            self.grammar = {}
            self.has_grammar = False
        # Load vocabulary
        self.vocab = self.getVocabularyFromGrammar()
        self.cmdVocabs = findCommandVocabularies(self.grammar)
        # Load grammar name
        self.grammar_name = path.basename(grammarFile)

    @staticmethod
    def loadGrammarName(filename):
        try:
            with open(filename) as f:
                return f.read().strip()
        except IOError:
            return '???'

    def getGrammarName(self):
        return self.grammar_name

    def getVocabularyFromGrammar(self):
        words = set()
        for branches in self.grammar.values():
            for outnode, transitions in branches.items():
                if outnode != 'is_terminal':
                    for transition in transitions:
                        nodeword = transition['outword'].lower()
                        words.add(nodeword)
        return words

    def convertDir(self, inputDir, outputDir, inputExtension='tra', outputExtension='xml', timeout=0, allowSkip=True,
                   isMBR=False, ignoreFluffWords=False):
        """
        Converts regular texts into their XML representation (given that they are grammatical).
        Reads in every file in inputDir that matches the file extension inputExtension (default "tra").
        The converted texts are saved to outputDir with filenames identical to their input equivalent,
        except for the extension, which is changed to outputExtension (default "xml")
        The output file has the same name as the input file, but with the extension
        defined by fileExtension (default: "xml")
        If a conversion failed, the respective file is not generated.
        Will not overwrite input file if dir and extension turn out to be identical.
        Other files (e.g. previous conversions) might be overwritten though.
        """
        for basename in os.listdir(inputDir):
            _, fileExtension = os.path.splitext(basename)
            if fileExtension[1:] == inputExtension:
                inputFile = path.join(inputDir, basename)
                self.convertFile(inputFile, outputDir, outputExtension, timeout=timeout, allowSkip=allowSkip,
                                 isMBR=isMBR, ignoreFluffWords=ignoreFluffWords)

    def convertFile(self, inputFile, outputDir, outputExtension='xml', timeout=0, allowSkip=True, isMBR=False,
                    ignoreFluffWords=False):
        """
        Converts a regular text into its XML representation (given that it is grammatical).
        Reads in the file inputFile and writes the result to outputDir.
        The output file has the same name as the input file, but with the extension
        defined by fileExtension (default: "xml")
        If the conversion failed, no file is generated.
        Will not overwrite the input file if dir and extension turn out to be identical.
        Other files (e.g. previous conversions) might be overwritten though.
        """
        basename = os.path.basename(inputFile)
        inputFilename, _ = os.path.splitext(basename)
        outputFilename = "{0}.{1}".format(inputFilename, outputExtension)
        outputFile = os.path.join(outputDir, outputFilename)

        if os.path.isfile(outputFile) and os.path.samefile(inputFile, outputFile):
            info = 'Input and output file would be identical, please change outputDir or fileExtension.'
            warn('{0} Current filename: {1}'.format(info, outputFile))
            return

        inputText = list()
        with open(inputFile) as f:
            if isMBR:
                tokens = list()
                for line in f:
                    items = line.strip().split()
                    word = items[-2]
                    confidence = items[-1]
                    token = word + CONF_SEPARATOR + confidence
                    token = str(token)
                    tokens.append(token)
                sentence = ' '.join(tokens)
                inputText.append(sentence)
            else:
                for line in f:
                    inputText.append(line.strip())

        if len(inputText) == 0:
            warn('File {0} appears to be empty'.format(inputFile))
        else:
            outputText = self.convertText(inputText, timeout=timeout, allowSkip=allowSkip,
                                          ignoreFluffWords=ignoreFluffWords)
            with open(outputFile, 'w') as w:
                w.write(outputText)

    def convertSentence(self, sentence, wxLib=None, wxGUI=None, timeout=0, allowSkip=True, ignoreFluffWords=False):
        """
        Converts a regular sentence into its XML representation, under the condition that it is weakly grammatical.
        A sentence is weakly grammatical when it can be made grammatical by ignoring some of its words.

        :param sentence: A string representing an utterance.
        :param wxLib:
        :param wxGUI:
        :param timeout: Number of seconds after which parsing will be aborted and the currently best parse returned.
                        If set 0 (default), no timeout will occur.
        :param allowSkip: If True (default) allow the parser to skip words to achieve a complete parse.
        :param ignoreFluffWords:
        :return: A string if conversion is successful, otherwise None.
        """
        sentence = str(sentence)
        return self.convertText([sentence], wxLib=wxLib, wxGUI=wxGUI, timeout=timeout, allowSkip=allowSkip,
                                ignoreFluffWords=ignoreFluffWords)

    def convertText(self, text, wxLib=None, wxGUI=None, timeout=0, allowSkip=True, ignoreFluffWords=False):
        """
        Converts a regular text into its XML representation, under the condition that it is weakly grammatical.
        A sentence is weakly grammatical when it can be made grammatical by ignoring some of its words.

        :param text: A list of strings
        :param wxLib:
        :param wxGUI:
        :param timeout: Number of seconds after which parsing will be aborted and the currently best parse returned.
                        If set 0 (default), no timeout will occur.
        :param allowSkip: If True (default) allow the parser to skip words to achieve a complete parse.
        :param ignoreFluffWords:
        :return: A string if conversion is successful, otherwise None.
        """
        xml_sentences = []
        for sentence in text:
            words = sentence.strip().split()
            trans = self.skipFST.getBestParse(words, self.grammar, cmdVocabularies=self.cmdVocabs, wxLib=wxLib,
                                              wxGUI=wxGUI, timeout=timeout, allowSkip=allowSkip, vocabulary=self.vocab,
                                              ignoreFluffWords=ignoreFluffWords)
            if trans is not None:
                xml = self.skipFST.addMissingWords(words, trans)
                xml_sentences.append(xml)
        xml = '\n'.join(xml_sentences)
        if len(xml.strip()) > 0:
            return xml
        else:
            return None