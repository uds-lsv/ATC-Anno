import sys
import re
import operator
import itertools
from os import path

from .FileTools import loadAirlineCallsigns
from functools import reduce

reOpenTag = re.compile('^<([a-z_]+)>$')
reOpenAnyTag = re.compile('^<([a-z_="]+)>$')
reCloseTag = re.compile('^</([a-z_]+)>$')
xml_temp = '.*<{0}>([a-z ]+)</{0}>.*'
xml_temp2 = '.*<{0}>([a-z ]*?)</{0}>.*'

reCmdStart = re.compile('<command=\"([a-z_]+)\">')
reConfid = re.compile(':[0-9].[0-9]*')

NO_CALLSIGN = 'NO_CALLSIGN'
NO_AIRLINE = 'NO_AIRLINE_'
UNKNOWN_AIRLINE = 'UNKNOWN_AIRLINE_'
NO_FLIGHTNUMBER = '_NO_FLIGHTNUMBER'
NO_CONCEPT = 'NO_CONCEPT'
DEFAULT_SCORE = 1.0
NOISE_TOKENS = ['_spn_', '_nsn_']
PRECISION = 4
CONF_SEPARATOR = ':'

CONFMODE_OFF = 0
CONFMODE_MIN = 1
CONFMODE_PROD = 2
CONFMODE_ARITMEAN = 3
CONFMODE_GEOMEAN = 4
CONFIDENCE_SUM_MODES = dict(off=CONFMODE_OFF,
                            min=CONFMODE_MIN,
                            prod=CONFMODE_PROD,
                            amean=CONFMODE_ARITMEAN,
                            gmean=CONFMODE_GEOMEAN)


def prod(iterable):
    return reduce(operator.mul, iterable, 1)


def reIsNotEmpty(re_groups, group_id=1):
    if re_groups is not None and len(re_groups.group(group_id).strip()) > 0:
        return True
    else:
        return False


def multiMatch(item, matchingFunctions, matchAll=False):
    """
    Convenience function to evaluate an item with a series of matching functions
    If matchAll is false, returns true if at least one matching function returns true.
    If matchAll is true, returns true if all matching functions return true.
    If matchingFunctions is an empty list, returns False.
    """
    if len(matchingFunctions) == 0:
        return False
    matches = [matchFunc(item) for matchFunc in matchingFunctions]
    if matchAll:
        return all(matches)
    else:
        return True in matches


def getAbsolutePath():
    # Ensure that script can be executed from anywhere (e.g. via python tools/GenerateConcept.py)
    scriptpath = path.dirname(sys.argv[0])
    return path.abspath(scriptpath)


def parseConfidenceMode(confidenceKey):
    return CONFIDENCE_SUM_MODES.get(confidenceKey, CONFMODE_OFF)


class TagFrame(object):
    """
    A token sequence surrounded by an XML tag.
    Each token consists of a word/tag string and a confidence value.
    """
    DEFAULT_CONFIDENCE = DEFAULT_SCORE

    def __init__(self, tokenPairs, isStrict=False):
        # Validate
        if isStrict:
            # Validate length of list
            if len(tokenPairs) < 2:
                raise ValueError("token pair list too short (len={0}): {1}".format(len(tokenPairs), tokenPairs))
            # Validate tokenPairs is a pair list
            if len(tokenPairs[0]) != 2:
                raise ValueError("Invalid format for token pairs: {0}".format(tokenPairs))

        # Validate a tag is surrounding the sentence
        firstToken = tokenPairs[0][0]
        lastToken = tokenPairs[-1][0]
        matchTag = reOpenTag.match(firstToken)
        matchCmd = reCmdStart.match(firstToken)

        # More validation
        if isStrict:
            if not (matchCmd and lastToken == '</command>') and not (
                    matchTag and lastToken == '</{0}>'.format(matchTag.group(1))):
                raise ValueError(
                    "First/Last item must be matching opening/closing xml tags: {0} ... {1}".format(firstToken,
                                                                                                    lastToken))

        self.tokenPairs = tokenPairs
        self.isStrict = isStrict

    def __str__(self):
        return 'TagFrame({0})'.format(self.tokenPairs)

    @classmethod
    def loadFromMBR(cls, inputFile, isStrict=False):
        # mdr input (one token per line, multiple token features)
        with open(inputFile) as f:
            tokenPairs = list()
            for line in f:
                items = line.strip().lower().split()
                token = items[-2]
                confidence = float(items[-1])
                tokenPairs.append((token, confidence))
        tokenPairs = cls.repairTagStructure(tokenPairs)
        return TagFrame(tokenPairs, isStrict)

    @classmethod
    def loadFromString(cls, string, isStrict=False):
        if string is None:
            return None

        string = str(string)  # Ensure we are dealing with str, not unicode object
        string = string.strip()
        string = string.replace('>', '> ')  # Workaround for faulty spacing around tags
        string = string.replace('<', ' <')
        string = string.replace('  ', ' ')

        stringtmp = ''
        for s in string.split():
            reSearch = re.search(reConfid, s)
            if reSearch is not None:
                stringtmp = stringtmp + ' ' + s.replace(reSearch.group(0), ' ' + reSearch.group(0) + ' ')
            else:
                stringtmp = stringtmp + ' ' + s
        string = (' '.join(stringtmp.split())).replace(' :', ':')

        string = string.strip().lower()
        if len(string) == 0:
            return None
        else:
            tokenPairs = list()
            for word in string.split():
                confidence = cls.DEFAULT_CONFIDENCE
                if CONF_SEPARATOR in word:
                    word, confidence = word.split(CONF_SEPARATOR, 1)
                    confidence = float(confidence)
                tokenPairs.append((word, confidence))
            tokenPairs = cls.repairTagStructure(tokenPairs)
            return TagFrame(tokenPairs, isStrict)

    @classmethod
    def repairTagStructure(cls, tokenPairs):
        """
        Fixes a sentence so that all XML tags that were opened are also closed at the end.
        Input is a list of token pairs (i.e. (word,confidence))
        """
        repairedTokenPairs = list()
        openTags = list()
        for tokenPair in tokenPairs:
            word = tokenPair[0]
            if reOpenAnyTag.match(word):
                # The only tags surrounding a command should be <s> and <commands>
                # Close all other tags when a new command starts
                if word.startswith('<command='):
                    while len(openTags) > 0:
                        if openTags[-1] in ['<s>', '<commands>']:
                            break
                        else:
                            openTag = openTags.pop()
                            cls._appendClosingPair_(repairedTokenPairs, openTag)

                # Add the opened tag
                openTags.append(word)
                repairedTokenPairs.append(tokenPair)
            elif reCloseTag.match(word):
                matchFound = False
                # Close all tags up until and including the current one
                while not matchFound and len(openTags) > 0:
                    openTag = openTags.pop()
                    cls._appendClosingPair_(repairedTokenPairs, openTag)
                    closeTag = repairedTokenPairs[-1][0]
                    if word == closeTag:
                        matchFound = True
                # In case there was no open tag matching the current closing tag
                # Put in a new opening tag and close it immediately.
                # This should make bug hunting easier than just not printing it.
                if not matchFound:
                    openPair = ('<' + word[2:], cls.DEFAULT_CONFIDENCE)
                    repairedTokenPairs.append(openPair)
                    repairedTokenPairs.append(tokenPair)  # This is the closing tag
            else:  # Regular word
                repairedTokenPairs.append(tokenPair)

        # If there are any tags left open, close them at the end of the utterance.
        while len(openTags) > 0:
            openTag = openTags.pop()
            cls._appendClosingPair_(repairedTokenPairs, openTag)

        return repairedTokenPairs

    @staticmethod
    def getClosingTag(openingTag):
        openRE = reOpenAnyTag.match(openingTag)
        if openRE:
            name = openRE.group(1)
            if name.startswith('command='):  # The ending tag for commands is just </command>
                name = 'command'
            closingTag = '</{0}>'.format(name)
        else:
            closingTag = None
        return closingTag

    @classmethod
    def _appendClosingPair_(cls, tokenPairs, openTag):
        """
        Convenience method that takes an opening tag,
        calculates its closing tag, turns it into a (tag,confidence) pair
        and appends it to the given token pair list.
        """
        closeTag = cls.getClosingTag(openTag)
        closePair = (closeTag, cls.DEFAULT_CONFIDENCE)
        tokenPairs.append(closePair)

    def __len__(self):
        return len(self.tokenPairs)

    def isEmptyTag(self):
        return len(self) <= 2

    def getFramePairs(self, contentOnly=False):
        if contentOnly:
            return self.tokenPairs[1:-1]
        else:
            return self.tokenPairs

    def getSplitPairs(self, contentOnly=False):
        tokenPairs = self.getFramePairs(contentOnly)
        tokens = list()
        confidences = list()
        for token, confidence in tokenPairs:
            tokens.append(token)
            confidences.append(confidence)
        return tokens, confidences

    def getTokens(self, contentOnly=False):
        tokenPairs = self.getFramePairs(contentOnly)
        return [token for token, _ in tokenPairs]

    def getConfidenceValues(self, contentOnly=False):
        tokenPairs = self.getFramePairs(contentOnly)
        return [confidence for _, confidence in tokenPairs]

    def getFramePair(self, i):
        return self.tokenPairs[i]

    def getToken(self, i):
        return self.tokenPairs[i][0]

    def getConfidenceValue(self, i):
        return self.tokenPairs[i][1]

    def containsTerm(self, term, termIsSet=False):
        if termIsSet:
            terms = term
        else:
            terms = [term]
        for token in self.getTokens():
            if token in terms:
                return True
        return False

    def toString(self, contentOnly=False):
        return ' '.join(self.getTokens(contentOnly=contentOnly))

    @staticmethod
    def _splitByMatch_(tokenPairs, matchFunc, n=0, assignMatchToHead=False):
        """
        Splits a list of tuples according to a matching function.
        The matching function is applied to the nth item of the tuple (default 0).
        Returns two lists. The second list starts with the matched item.
        If no match was found, returns the full list as first element and None as second element.
        """
        for i in range(len(tokenPairs)):
            token = tokenPairs[i][n]
            if matchFunc(token):
                if assignMatchToHead:
                    head = tokenPairs[:i + 1]
                    tail = tokenPairs[i + 1:]
                else:
                    head = tokenPairs[:i]
                    tail = tokenPairs[i:]
                return head, tail
        # In case no item matched
        return tokenPairs, None

    def _extractSubframe_(self, startMatch, endMatch, n=0):
        head, rest = TagFrame._splitByMatch_(self.tokenPairs, startMatch, n=n, assignMatchToHead=False)
        if rest is None:
            return None, self
        body, tail = TagFrame._splitByMatch_(rest, endMatch, n=n, assignMatchToHead=True)
        extractedFrame = TagFrame(body, self.isStrict)
        outerFrame = TagFrame(head + tail, self.isStrict)
        return extractedFrame, outerFrame

    def extractTag(self, tag, is_command=False):
        """
        Extracts a tag frame from a parent tag frame.
        tag is the name of the tag to be extracted.
        If is_command is true, tag is used as <command="{tag}"> rather than as the tag itself.
        Returns a tuple (extractedFrame, outerFrame), where the former is the
            extracted tag frame and the later is the remaindr of the parent frame
            without the extracted bit.
        """
        if is_command:
            startTag = '<command="{0}">'.format(tag)
            endTag = '</command>'
        else:
            startTag = '<{0}>'.format(tag)
            endTag = '</{0}>'.format(tag)
        return self._extractSubframe_(startTag.__eq__, endTag.__eq__, n=0)

    def extractCommand(self):
        """
        Extract the first command found inside the frame.
        Returns a tuple (extractedFrame, outerFrame), where the former is the
            extracted command frame and the later is the remaindEr of the parent frame
            without the extracted bit.
        """
        return self._extractSubframe_(reCmdStart.match, '</command>'.__eq__, n=0)

    def extractNoise(self, startMatches=None, endMatches=None, contentOnly=False):
        """
        Extracts all noise tokens (and their confidences) from a frame.
        To match TagFrame format, the tokens are surrounded by artificial <noise> tags.
        startMatches is a list of truth functions.
           Noise is only extracted after one of the functions has returned true for a token.
           If startMatches is None, search starts immediately from the start.
        endMatches is a list of truth functions.
           Noise is only extracted until one of the functions has returned true for a token.
           If endMatches is None, search continues until the end of the frame.
        """
        noiseSequence = [('<noise>', 1.0)]
        if startMatches is None:
            hasStarted = True  # If no start string is given, start immediately
            startMatches = []
        else:
            hasStarted = False
        if endMatches is None:
            endMatches = []

        for pair in self.getFramePairs(contentOnly):
            token = pair[0]
            if not hasStarted:  # Before noise sequence
                # Look for start of search space 
                if multiMatch(token, startMatches):
                    hasStarted = True
                    continue
            else:  # Part of noise sequence
                # Check if search space has ended
                if multiMatch(token, endMatches):
                    break
                else:
                    # Append noise words to sequence
                    if token in NOISE_TOKENS:
                        noiseSequence.append(pair)

        noiseSequence.append(('</noise>', 1.0))

        return TagFrame(noiseSequence, self.isStrict)

    def filterFrame(self):
        filteredTokens = [self.getFramePair(0)]
        activeTag = None
        for tokenPair in self.getFramePairs(contentOnly=True):
            token = tokenPair[0]
            m_open = reOpenTag.match(token)
            if m_open:  # Opening a tag
                activeTag = m_open.group(1)
            elif activeTag is None:  # Token is outside of tag and not a tag itself
                filteredTokens.append(tokenPair)
            elif token == '</{0}>'.format(activeTag):  # Token is closing the open tag
                activeTag = None
        filteredTokens.append(self.getFramePair(-1))
        return TagFrame(filteredTokens, self.isStrict)

    def getConfidenceScore(self, mode, contentOnly=False):
        return self.getJointConfidenceScore([self], mode, contentOnly)

    @staticmethod
    def getJointConfidenceScore(frames, mode, contentOnly=False):
        confidences = list()
        for frame in frames:
            if frame is not None:
                confidences.extend(frame.getConfidenceValues(contentOnly))

        if mode is CONFMODE_OFF:
            return None
        else:
            if len(confidences) == 0:
                return -1.0
            else:
                if mode is CONFMODE_MIN:
                    return min(confidences)
                elif mode is CONFMODE_PROD:
                    return prod(confidences)
                elif mode is CONFMODE_ARITMEAN:
                    return (1.0 / len(confidences)) * sum(confidences)
                elif mode is CONFMODE_GEOMEAN:
                    return pow(prod(confidences), 1.0 / len(confidences))
                else:
                    e = "Unknown confidence calculation mode: {0}".format(mode)
                    raise ValueError(e)


class UtteranceUnit(object):
    """
    Super class for utterance units.
    Don't use directly, instead refer to e.g. Callsign or Command.
    """
    LETTERS = dict(alpha='A', bravo='B', charly='C',
                   delta='D', echo='E', foxtrot='F', fox='F',
                   golf='G', hotel='H', india='I',
                   juliett='J', kilo='K', lima='L',
                   mike='M', november='N', oscar='O',
                   papa='P', quebec='Q', romeo='R',
                   sierra='S', tango='T', uniform='U',
                   victor='V', whisky='W', xray='X',
                   yankee='Y', zoulou='Z')
    LETTERWORDS = dict()
    for word, letter in LETTERS.items():
        LETTERWORDS.setdefault(letter, list()).append(word)

    SINGLE_DIGITS = dict(one='1', two='2', three='3',
                         four='4', five='5', six='6',
                         seven='7', eight='8', nine='9',
                         zero='0')
    TEEN_DIGITS = dict(eleven='one', twelve='two', thirteen='three',
                       fourteen='four', fifteen='five', sixteen='six',
                       seventeen='seven', eightteen='eight', nineteen='nine')
    TENS_DIGITS = dict(ten='one', twenty='two', thirty='three',
                       forty='four', fifty='five', sixty='six',
                       seventy='seven', eighty='eight', ninety='nine')
    SPECIAL_DIGITS = dict(hundred='00', thousand='000')
    DOUBLE_DIGITS = dict(ten='10', twenty='20', thirty='30',
                         forty='40', fifty='50', sixty='60',
                         seventy='70', eighty='80', ninety='90')
    MULTIPLIERS = dict(double=2, triple=3)
    ALPHANUM = dict()
    ALPHANUM.update(SINGLE_DIGITS)
    ALPHANUM.update(LETTERS)
    ALPHANUM['decimal'] = '.'

    # Set of all words that can come up in a numbers context
    # This includes numbers, letters and special words like "and", "decimal" or "hundred"
    NUMBER_VOCABULARY = set(ALPHANUM)
    NUMBER_VOCABULARY.update(TEEN_DIGITS)
    NUMBER_VOCABULARY.update(TENS_DIGITS)
    NUMBER_VOCABULARY.update(SPECIAL_DIGITS)
    NUMBER_VOCABULARY.update(DOUBLE_DIGITS)
    NUMBER_VOCABULARY.update(MULTIPLIERS)
    NUMBER_VOCABULARY.add('and')

    def __init__(self, utteranceFrame):
        self.utteranceFrame = utteranceFrame

    def __str__(self):
        return 'UtteranceUnit({0})'.format(self.utteranceFrame)

    def parseNumber(self, numberFrame, isFrame=True):
        """
        Converts a series of words into the correct digit representation.
        If isFrame is true, numberFrame must be a TagFrame object,
           if false, numberFrame must be a list of word strings.
        """
        if isFrame:
            items = numberFrame.getTokens(contentOnly=True)
        else:
            items = numberFrame

        # Filter out rogue words (e.g. silence markers and hesitation words)
        items = [item for item in items if (item in self.NUMBER_VOCABULARY)]

        # Prepare
        while 'and' in items:  # Remove "and"
            items.remove('and')
        # if speaker dropped the digit before thousand/hundred, it was a one
        if len(items) > 0 and items[0] in ['thousand', 'hundred']:
            items.insert(0, 'one')
        # Thousands
        while 'thousand' in items:
            i = items.index('thousand')
            nxt = i + 1
            nxt2 = nxt + 1
            # thousand was last item
            if nxt >= len(items):
                items.extend(['zero'] * 3)
            # if "x thousand y hundred", move on to parse "x y hundred"
            elif nxt2 < len(items) and items[nxt2] == 'hundred':
                pass
            else:
                if items[nxt] not in self.TENS_DIGITS and items[nxt] not in self.TEEN_DIGITS:
                    if items[nxt] not in self.SINGLE_DIGITS:
                        items.insert(nxt, 'zero')
                    items.insert(nxt, 'zero')
                items.insert(nxt, 'zero')
            items.pop(i)
        # Hundreds
        while 'hundred' in items:
            i = items.index('hundred')
            nxt = i + 1
            if nxt >= len(items):  # hundred was last item
                items.extend(['zero'] * 2)
            elif items[nxt] not in self.TENS_DIGITS and items[nxt] not in self.TEEN_DIGITS:
                if items[nxt] not in self.SINGLE_DIGITS:
                    items.insert(nxt, 'zero')
                items.insert(nxt, 'zero')
            items.pop(i)

        for multiple, multiplier in self.MULTIPLIERS.items():
            while multiple in items:
                i = items.index(multiple)
                nxt = i + 1
                if nxt < len(items):  # multiple wasn't last word
                    next_item = items[nxt]
                    for _ in range(1, multiplier):
                        items.insert(nxt, next_item)
                items.pop(i)

        # Tens
        for tenner, tenval in self.TENS_DIGITS.items():
            while tenner in items:
                i = items.index(tenner)
                nxt = i + 1
                items[i] = tenval
                if nxt >= len(items):  # Tenner was last item
                    items.append('zero')
                elif items[nxt] not in self.SINGLE_DIGITS or items[nxt] == 'zero':
                    items.insert(nxt, 'zero')
        # Teen values (11-19)
        for teen, teenval in self.TEEN_DIGITS.items():
            while teen in items:
                i = items.index(teen)
                items[i] = teenval
                items.insert(i, 'one')
        # Parse
        num = []
        for i, item in enumerate(items):
            if item in self.ALPHANUM:
                num += self.ALPHANUM[item]
        return ''.join(num)


class Callsign(UtteranceUnit):
    """
    Contains all information on the callsign part of an utterance.
    """
    AIRLINE_ALT_SPELLING = {"air_frans": "air_france",
                            "hansa": "lufthansa"}

    def __init__(self, callsignFrame, airlineShorts, contextFile=None, isStrict=False):
        """
        :param callsignFrame: must be a TagFrame for a callsign tag
        :param airlineShorts: is a dictionary of the official acronyms of airline names
        :param contextFile: is the filename of the current_callsign file
        :param isStrict:
        """
        super(Callsign, self).__init__(callsignFrame)

        self.airlineShorts = airlineShorts
        self.contextFile = contextFile
        self.callsignFrame = self.utteranceFrame
        self.noiseFrame = None

        if callsignFrame is None:
            self.airlineFrame = None
            self.flightnumberFrame = None
            self.remainderFrame = None
            self.callsign = NO_CALLSIGN
        else:
            if isStrict and not self.isCallsignFrame(callsignFrame):
                raise ValueError("Invalid format for callsign frame: " + str(self.callsignFrame))

            self.airlineFrame, self.flightnumberFrame, self.remainderFrame = self._extractSubframes_(self.callsignFrame)
            self.callsign = self._computeCallsign_(self.airlineFrame, self.flightnumberFrame, self.contextFile)

    def __str__(self):
        return 'Callsign({0})'.format(self.utteranceFrame)

    def isCallsign(self):
        return self.callsign != NO_CALLSIGN

    @staticmethod
    def isCallsignFrame(callsignFrame):
        return callsignFrame.getToken(0) == '<callsign>' and callsignFrame.getToken(-1) == '</callsign>'

    @staticmethod
    def _extractSubframes_(callsignFrame):
        airlineFrame, remainderFrame = callsignFrame.extractTag("airline", is_command=False)
        flightnumberFrame, _ = remainderFrame.extractTag("flightnumber", is_command=False)
        return airlineFrame, flightnumberFrame, remainderFrame

    def _computeCallsign_(self, airlineFrame, flightnumberFrame, contextFile=None):
        # Check for empty tags
        hasFlightnumber = False
        if flightnumberFrame is not None:
            hasFlightnumber = not flightnumberFrame.isEmptyTag()

        hasAirline = False
        if airlineFrame is not None:
            hasAirline = not airlineFrame.isEmptyTag()

        # If no callsign info exists, abort
        if not hasAirline and not hasFlightnumber:
            return NO_CALLSIGN

        # ### Prepare output string ###
        # Parse whichever part is not unknown
        if hasAirline:
            airlineWord = ' '.join(airlineFrame.getTokens(contentOnly=True))
            if airlineWord in self.AIRLINE_ALT_SPELLING:  # Hotfix for typical annotator misspelling
                airlineWord = self.AIRLINE_ALT_SPELLING[airlineWord]
            airsign = self.airlineShorts.get(airlineWord, UNKNOWN_AIRLINE)
        else:
            airlineWord = NO_AIRLINE
            airsign = NO_AIRLINE

        if hasFlightnumber:
            flightsign = self.parseNumber(flightnumberFrame)
        else:
            flightsign = NO_FLIGHTNUMBER

        # Try autocompleting callsign information 
        airsign, flightsign, _ = self.autocompleteCallsign(airsign, flightsign, airlineWord=airlineWord,
                                                           contextFile=contextFile)

        callsign = airsign + flightsign
        return callsign

    @staticmethod
    def loadContext(contextFile):
        callsigns = []
        if contextFile is not None:
            with open(contextFile) as f:
                for line in f:
                    line = line.strip()
                    if len(line) > 0:
                        elems = line.split()
                        if len(elems) >= 2:
                            airline, flightnumber = elems
                        else:
                            airline = ''
                            flightnumber = elems[0]
                        callsigns.append((airline, flightnumber))

        return callsigns

    def autocompleteCallsign(self, airsign, flightsign, airlineWord=None, contextFile=None):
        """
        When airline or flightnumber are unknown, check the callsign context file
        for possible partial matches.
        Additional functions:
        1. Checks whether ATC might have dropped a leading zero (e.g. AMB1 for AMB01)
           and restores it, both for complete and incomplete callsigns.
        2. Check whether airline could be leading flight number letter (and vice versa)
        Returns a triple (airsign, flightnumber, is_changed)
        """
        if self.contextFile is not None:
            context = self.loadContext(contextFile)
            ambiguous_airsigns, ambiguous_flightsigns = self.getAmbiguousCallsigns(context)
            # ### Complete Callsign ###
            # Only checks for missing leading zeroes
            if airsign is not NO_AIRLINE and flightsign != NO_FLIGHTNUMBER:
                # First check if the regular callsign is in the context,
                # then check if a leading zero was dropped (e.g. AMB1 for AMB01).
                for this_flightsign in [flightsign, '0' + flightsign]:
                    for ctx_airsign, ctx_flightsign in context:
                        if ctx_airsign == airsign and this_flightsign == ctx_flightsign:
                            return airsign, this_flightsign, True
            # ### Missing Callsign ###
            # Tries to autocomplete the callsign and checks for missing leading zeroes.
            elif flightsign != NO_FLIGHTNUMBER:
                # First check if the regular flightnumber is in the context,
                # then check if a leading zero was dropped (e.g. AMB1 for AMB01).
                for this_flightsign in [flightsign, '0' + flightsign]:
                    if this_flightsign not in ambiguous_flightsigns:
                        for ctx_airsign, ctx_flightsign in context:
                            if this_flightsign == ctx_flightsign:
                                return ctx_airsign, this_flightsign, True
            # ### Missing Flightnumber ###
            # Tries to autocomplete the flightnumber
            elif airsign != NO_AIRLINE:
                if airsign not in ambiguous_airsigns:
                    for ctx_airsign, ctx_flightsign in context:
                        if airsign == ctx_airsign:
                            return airsign, ctx_flightsign, True

        # Check whether airline should be a letter
        if airlineWord is not None and airlineWord in self.LETTERS:
            alt_airsign = NO_AIRLINE
            alt_flightsign = self.LETTERS[airlineWord] + flightsign
            new_airsign, new_flightsign, is_changed = self.autocompleteCallsign(alt_airsign, alt_flightsign,
                                                                                airlineWord=None,
                                                                                contextFile=contextFile)
            if is_changed:
                return new_airsign, new_flightsign, is_changed

        # Check whether first letter should be an airline
        if len(flightsign) == 0:
            first_letter = NO_FLIGHTNUMBER
        else:
            first_letter = flightsign[0]

        if airsign == NO_AIRLINE and flightsign != NO_FLIGHTNUMBER and first_letter in self.LETTERWORDS:
            for letterword in self.LETTERWORDS[first_letter]:
                if letterword in self.airlineShorts:
                    alt_airsign = self.airlineShorts[letterword]
                    alt_flightsign = flightsign[1:]
                    new_airsign, new_flightsign, is_changed = self.autocompleteCallsign(alt_airsign, alt_flightsign,
                                                                                        airlineWord=None,
                                                                                        contextFile=contextFile)
                    if is_changed:
                        return new_airsign, new_flightsign, is_changed

        # In case of no good context (or no need for fixing anything), autocomplete fails and returns originals
        return airsign, flightsign, False

    @staticmethod
    def getAmbiguousCallsigns(context):
        """
        Checks the current callsign context for ambiguous airlines and flightnumber.
        Returns a tuple of sets: (ambiguous_airsigns, ambiguous_flightsigns)
        """
        seen_airsigns = set()
        seen_flightsigns = set()
        ambiguous_airsigns = set()
        ambiguous_flightsigns = set()
        for ctx_airsign, ctx_flightsign in context:
            if ctx_airsign in seen_airsigns:
                ambiguous_airsigns.add(ctx_airsign)
            else:
                seen_airsigns.add(ctx_airsign)

            if ctx_flightsign in seen_flightsigns:
                ambiguous_flightsigns.add(ctx_flightsign)
            else:
                seen_flightsigns.add(ctx_flightsign)
        return ambiguous_airsigns, ambiguous_flightsigns

    @staticmethod
    def extractPreCommandNoise(sentenceFrame):
        startMatches = ['<s>'.__eq__]
        endMatches = ['callsign'.__eq__, reCmdStart.match]
        return sentenceFrame.extractNoise(startMatches, endMatches, contentOnly=False)

    def addNoise(self, sentenceFrame):
        noiseFrame = self.extractPreCommandNoise(sentenceFrame)
        self.noiseFrame = noiseFrame

    def getCallsign(self):
        return self.callsign

    def getConfidenceScore(self, mode, contentOnly):
        return TagFrame.getJointConfidenceScore(self.getFrames(), mode, contentOnly=contentOnly)

    def getFrames(self):
        if self.isCallsign():  # Regular case: Return airline and flightnumber frames
            return [self.airlineFrame, self.flightnumberFrame]
        else:  # In case no callsign information exists, return pre-command noise.
            return [self.noiseFrame]


class Command(UtteranceUnit):
    """
    Contains all information for a single command, including the callsign it is referring to.
    """
    # Prepare dicts
    DIRECTIONS = dict(left='L', right='R')

    LIMIT_POSITIVE = ['above', 'more', 'greater', 'least']
    LIMIT_NEGATIVE = ['below', 'less', 'most']

    WAYPOINTS = dict(metma='METMA',
                     regno='REGNO',
                     domux='DOMUX',
                     waypoint='Waypoint')

    def __init__(self, commandFrame, callsign, isStrict=False):
        """
        commandFrame must be a TagFrame surrounded by a command tag.
        callsign must be a Callsign object.
        """
        super(Command, self).__init__(commandFrame)

        # Prepare variables
        self.isLegal = False  # True if information suffices to generate a command
        self.commandFrame = self.utteranceFrame
        self.callsignObject = callsign
        self.callsign = callsign.getCallsign()
        self.conceptFrame = None  # Concept frame = command frame without
        self.concept = None
        self.valueFrame = None
        self.value = None
        self.metricFrame = None
        self.metric = None

        if commandFrame is None:
            self.isLegal = True
            self.concept = NO_CONCEPT
        else:
            if isStrict and not self.isCommandFrame(commandFrame):
                raise ValueError("Invalid format for callsign frame: " + commandFrame.toString())
            self.computeCommand()

    def __str__(self):
        return 'Command({0})'.format(self.utteranceFrame)

    @staticmethod
    def isCommandFrame(commandFrame):
        isCmd = reCmdStart.match(commandFrame.getToken(0))
        return isCmd and commandFrame.getToken(-1) == '</command>'

    def getCommandType(self):
        m = reCmdStart.match(self.commandFrame.getToken(0))
        cmdType = m.group(1)

        return cmdType

    def parseRunway(self, runwayFrame):
        items = runwayFrame.getTokens(contentOnly=True)

        runway = ''
        for item in items:
            if item in self.ALPHANUM:
                runway += self.ALPHANUM[item]
            elif item in self.DIRECTIONS:
                runway += self.DIRECTIONS[item]
        # Beware that the runway parser currently doesn't check number formatting, e.g. leading zeroes are not removed.
        return runway

    def parseWaypoint(self, waypointFrame):
        fix = ''
        items = waypointFrame.getTokens(contentOnly=True)
        while 'waypoint' in items:
            items.remove('waypoint')
        if len(items) > 0:
            first_item = items[0].lower()
            # Process first word(s)
            if first_item == 'lima':  # Fix for using "lima" as short of "lima mike alpha"
                if len(items) < 3 or not (items[1].lower() == 'mike' and items[2].lower() == 'alpha'):
                    fix += 'LMA'
                    items = items[1:]
            elif first_item in self.SINGLE_DIGITS or first_item in self.SPECIAL_DIGITS:
                fix += "DL"
            elif first_item in self.WAYPOINTS:
                fix += self.WAYPOINTS[first_item]
                items = items[1:]

        fix += self.parseNumber(items, isFrame=False)
        return fix

    def parseFrequency(self, frequencyFrame):
        num = self.parseNumber(frequencyFrame)
        if '.' not in num and len(num) > 3:
            num = '{}.{}'.format(num[:3], num[3:])  # Assume that frequencies have three digits before the decimal point
        return num

    def parseLimit(self, commandFrame, directionTerms, directionCmd, boundaryTerms, boundaryCmd):
        # Check if limit was inverted through negation
        negFrame, remainderFrame = commandFrame.extractTag('neg')
        if negFrame is not None:
            (directionTerms, boundaryTerms) = (boundaryTerms, directionTerms)
        # Check if direction term was used (i.e. value can go in that direction)
        directionTag, remainderFrame = remainderFrame.extractTag(directionTerms)
        if not self._isEmptyFrame_(directionTag):
            return directionCmd
        else:
            # Check if boundary term was used (i.e. value can NOT go in that direction)
            boundaryTag, remainderFrame = remainderFrame.extractTag(boundaryTerms)
            if not self._isEmptyFrame_(boundaryTag):
                return boundaryCmd
            else:
                return self.parseLimitUntagged(remainderFrame, directionTerms, directionCmd, boundaryTerms, boundaryCmd)

    @staticmethod
    def parseLimitUntagged(commandFrame, directionTerms, directionCmd, boundaryTerms, boundaryCmd):
        """
        Hack that allows above/below extensions of commands to be found when  they are not tagged, as long as they are
        inside the command tag.
        """
        if commandFrame.containsTerm(directionTerms, termIsSet=True):
            return directionCmd
        else:
            if commandFrame.containsTerm(boundaryTerms, termIsSet=True):
                return boundaryCmd
            else:
                return ""

    def parseAbove(self, commandFrame):
        """
        Parse limit terms (e.g. more, less, above...) for a command that expects an _X_ABOVE extension.
        """
        return self.parseLimit(commandFrame, self.LIMIT_POSITIVE, '_OR_ABOVE', self.LIMIT_NEGATIVE, '_NOT_ABOVE')

    def parseBelow(self, commandFrame):
        """
        Parse limit terms (e.g. more, less, above...) for a command that expects an _X_BELOW extension.
        """
        return self.parseLimit(commandFrame, self.LIMIT_NEGATIVE, '_OR_BELOW', self.LIMIT_POSITIVE, '_NOT_BELOW')

    def parseEitherLimit(self, commandFrame):
        """
        Parse limit terms (e.g. more, less, above...) for a command that expects an _OR_X extension
        (X being ABOVE or BELOW).
        """
        return self.parseLimit(commandFrame, self.LIMIT_POSITIVE, '_OR_ABOVE', self.LIMIT_NEGATIVE, '_OR_BELOW')

    @staticmethod
    def parseDirection(directionFrame):
        """
        Parse a given direction-tag frame and return a string for the contained direction info.
        """
        directionTokens = directionFrame.getTokens(contentOnly=True)
        direction = '_'.join(directionTokens)
        return direction.upper()

    @staticmethod
    def parseContact(contactFrame):
        """
        Parse a given contact-tag frame and return a string for the contained contact info.
        """
        contactTokens = contactFrame.getTokens(contentOnly=True)
        contact = '_'.join(contactTokens)
        return contact.upper()

    @staticmethod
    def _isEmptyFrame_(frame):
        """
        Returns true if frame is either None or if it is a frame which is empty except for its tags
        """
        return frame is None or frame.isEmptyTag()

    def computeCommand(self):
        """
        Generates the concept and value for the command and combines it with callsign to return the AMAN command for
        this command object.
        """
        cmdType = self.getCommandType()
        concept = cmdType.upper()
        conceptFrame = self.commandFrame
        # Look up and interpret command

        # ############# Relevant commands ##############
        if cmdType in ['descend', 'climb', 'give_altitude', 'maintain_altitude',
                       'rate_of_descent', 'rate_of_climb',
                       'reduce', 'increase', 'give_speed', 'maintain_speed',
                       'speed_own', 'reduce_final_app', 'reduce_min_clean',
                       'turn', 'turn_heading', 'heading',
                       'transition', 'direct_to',
                       'cleared_ils',
                       'handover']:
            # Specify value frames
            # Each tag set is a list of tuples (tagString, metricString).
            # tagString is the name of the xml tag (e.g. speed for <speed>)
            # metricString is the command's metric, given after the value (e.g. DESCEND 3000 ALT)
            # If the command does not require metric info. use None for metricString
            heightTags = [('flightlevel', 'FL'), ('altitude', 'ALT')]
            fpmTags = [('feet_per_minute', None)]
            speedTags = [('speed', None)]
            degreeRelTags = [('degree_relative', None)]
            degreeAbsTags = [('degree_absolute', None)]
            waypointTags = [('waypoint', None), ('fix', None)]
            runwayTags = [('runway', None)]
            contactTags = [('contact', None), ('frequency', None)]
            # Lists of xml tags to check for, depending on which command we are in
            cmd2tag = [(['descend', 'climb', 'give_altitude', 'maintain_altitude'], heightTags),
                       (['rate_of_descent', 'rate_of_climb'], fpmTags),
                       (['reduce', 'increase', 'give_speed', 'maintain_speed'], speedTags),
                       (['turn'], degreeRelTags),
                       (['turn_heading', 'heading'], degreeAbsTags),
                       (['transition', 'direct_to'], waypointTags),
                       (['cleared_ils'], runwayTags),
                       (['handover'], contactTags)]

            tagFrame = None
            tag = None
            metric = None
            for cmdTypes, tags in cmd2tag:
                if cmdType in cmdTypes:
                    for tagString, metricString in tags:
                        tagFrame, conceptFrame = self.commandFrame.extractTag(tagString)
                        if tagFrame is not None:
                            tag = tagString
                            metric = metricString
                            break  # Interrupt search when tag frame is found

            # Commands that can be parsed without a value
            if cmdType in ['maintain_altitude', 'maintain_speed',
                           'speed_own', 'reduce_final_app', 'reduce_min_clean']:
                self.concept = concept
                self.isLegal = True  # Can be true without parsing value information

            # Parse concept and value information
            if self._isEmptyFrame_(tagFrame):
                # For commands that change behaviour when they are missing a value.
                if cmdType in ['heading']:
                    self.concept = 'MAINTAIN_HEADING'
                    self.isLegal = True
            else:
                # Parse command type modifiers
                if cmdType in ['descend', 'reduce', 'rate_of_climb']:
                    concept += self.parseBelow(conceptFrame)
                elif cmdType in ['increase', 'climb', 'rate_of_descent']:
                    concept += self.parseAbove(conceptFrame)
                elif cmdType in ['give_altitude', 'give_speed']:
                    concept = cmdType[5:].upper()  # Cutting off the give_ part
                    concept += self.parseEitherLimit(conceptFrame)
                elif cmdType in ['maintain_speed']:
                    limit = self.parseEitherLimit(conceptFrame)
                    if limit != '':
                        concept = 'SPEED' + limit
                elif cmdType in ['maintain_altitude']:
                    limit = self.parseEitherLimit(conceptFrame)
                    if limit != '':
                        concept = 'ALTITUDE' + limit
                elif cmdType in ['turn', 'turn_heading']:
                    directionFrame, _ = conceptFrame.extractTag('direction')
                    if self._isEmptyFrame_(directionFrame):
                        if cmdType in ['turn']:
                            concept = 'TURN_BY'
                        elif cmdType in ['turn_heading']:
                            concept = None  # Turn_Heading requires direction information
                    else:
                        direction = self.parseDirection(directionFrame)
                        if cmdType in ['turn']:
                            concept = 'TURN_{0}_BY'.format(direction)
                        elif cmdType in ['turn_heading']:
                            concept = 'TURN_{0}_HEADING'.format(direction)

                # Parse value
                if tag in ['waypoint', 'fix']:
                    value = self.parseWaypoint(tagFrame)
                elif tag in ['runway']:
                    value = self.parseRunway(tagFrame)
                elif tag in ['contact']:
                    value = self.parseContact(tagFrame)
                elif tag in ['frequency']:
                    value = self.parseFrequency(tagFrame)
                    concept = 'HANDOVER_FREQUENCY'
                else:
                    value = self.parseNumber(tagFrame)
                    try:
                        # Transform to int and back to string to get rid of leading zeroes etc.
                        value = str(int(value))
                    except ValueError:
                        pass  # If string-integer double transform fails, just use the regular word

                # Update object variables
                self.concept, self.value, self.metric = concept, value, metric
                self.isLegal = True

        # ############# Irrelevant commands ##############
        elif cmdType in ['init_response', 'report_speed', 'expect_ils', 'vector',
                         'information', 'report_established', 'touchdown']:
            self.concept = NO_CONCEPT
            self.isLegal = True
            tagFrame = None

        # ############# Unknown commands ##############
        else:
            self.concept = NO_CONCEPT
            self.isLegal = False
            tagFrame = None

        conceptFrame = conceptFrame.filterFrame()
        self.conceptFrame = conceptFrame
        self.valueFrame = tagFrame

    def getAMANString(self, mode, getConfidence=True, getSubconfidences=True):
        contentOnly = True
        items = list()
        sources = [(self.callsign, self.getCallsignConfidenceScore),
                   (self.concept, self.getConceptConfidenceScore),
                   (self.value, self.getValueConfidenceScore),
                   (self.metric, self.getMetricConfidenceScore)]

        if mode == CONFMODE_OFF:
            getConfidence = False
            getSubconfidences = False

        # Append tokens
        for token, scoreFunc in sources:
            if token is not None:
                items.append(token)

        # Get subconfidences
        if getSubconfidences:
            for token, scoreFunc in sources[:-1]:
                score = scoreFunc(mode, contentOnly)
                if score is None:
                    score = DEFAULT_SCORE
                else:
                    score = round(score, PRECISION)
                items.append(str(score))

        # Append total confidence
        if getConfidence:
            score = self.getTotalConfidenceScore(mode, contentOnly)
            if score is None:
                score = DEFAULT_SCORE
            else:
                score = round(score, PRECISION)
            items.append(str(score))

        return ' '.join(items)

    def getTotalConfidenceScore(self, mode, contentOnly):
        """
        Returns the confidence score for the entire command (including callsign).
        """
        frames = list()
        funcs = self.callsignObject.getFrames()
        funcs.extend([self.conceptFrame,
                      self.valueFrame,
                      self.metricFrame])
        for frame in funcs:
            if frame is not None:
                frames.append(frame)
        return TagFrame.getJointConfidenceScore(frames, mode, contentOnly=contentOnly)

    def getCallsignConfidenceScore(self, mode, contentOnly):
        return self.callsignObject.getConfidenceScore(mode, contentOnly)

    def getConceptConfidenceScore(self, mode, contentOnly):
        frames = [self.conceptFrame]
        return TagFrame.getJointConfidenceScore(frames, mode, contentOnly=contentOnly)

    def getValueConfidenceScore(self, mode, contentOnly):
        if self.valueFrame is None:
            return None
        else:
            frames = [self.valueFrame]
            return TagFrame.getJointConfidenceScore(frames, mode, contentOnly=contentOnly)

    def getMetricConfidenceScore(self, mode, contentOnly):
        if self.metricFrame is None:
            return None
        else:
            frames = [self.metricFrame]
            return TagFrame.getJointConfidenceScore(frames, mode, contentOnly=contentOnly)


class CommandSet:
    """
    Collection of all commands of an utterance.
    """

    def __init__(self, sentenceFrame, airlineShorts, contextFile, isStrict=False):
        """
        :param sentenceFrame: TagFrame of the entire utterance (i.e. the <s> tag)
        :param airlineShorts: dictionary of the official acronyms of airline names
        :param contextFile: filename of the current_callsign file.
        :param isStrict:
        """
        self.callsign, self.commands = self._extractData_(sentenceFrame, airlineShorts, contextFile, isStrict)

    @staticmethod
    def _extractData_(sentenceFrame, airlineShorts, contextFile, isStrict):
        cmds = list()
        if sentenceFrame is None:
            callsign = Callsign(None, airlineShorts, contextFile, isStrict=isStrict)
        else:
            # generate callsign object
            callsignFrame, remainderFrame = sentenceFrame.extractTag("callsign", is_command=False)
            callsign = Callsign(callsignFrame, airlineShorts, contextFile, isStrict=isStrict)
            if not callsign.isCallsign():
                callsign.addNoise(sentenceFrame)

            # extract command frames
            cmdFrames = list()
            while True:
                cmdFrame, remainderFrame = remainderFrame.extractCommand()
                if cmdFrame is None:
                    break
                else:
                    cmdFrames.append(cmdFrame)

            # generate command objects
            for cmdFrame in cmdFrames:
                cmd = Command(cmdFrame, callsign, isStrict=isStrict)
                #                 if cmd.concept != NO_CONCEPT:
                if cmd.isLegal:
                    cmds.append(cmd)

            # No commands found
            if len(cmds) == 0:
                cmd = Command(None, callsign, isStrict=isStrict)
                cmds.append(cmd)
        return callsign, cmds

    def __str__(self):
        lines = ['CommandSet(',
                 '  {0}'.format(self.callsign),
                 '  Commands:']
        if len(self.commands) == 0:
            lines.append('    None')
        else:
            for cmd in self.commands:
                lines.append('    {0}'.format(cmd))
        string = '\n'.join(lines)
        string += ')'
        return string

    def getCallsign(self):
        print(self.callsign)

    def getCommands(self):
        print(self.commands)

    def addCommand(self, command):
        self.commands.append(command)

    def getAMANStrings(self, mode, getConfidence=True, getSubconfidences=True):
        """
        Get a list of strings, each representing a single command in AMAN-interface format.
        """
        # Split into proper (legal) commands and NO_CONCEPT instances
        contentOnly = True
        conceptCmds = list()
        noConceptCmds = list()
        for cmd in self.commands:
            if cmd.concept != NO_CONCEPT:
                conceptCmds.append(cmd)
            else:
                noConceptCmds.append(cmd)

        outputs = list()
        if len(conceptCmds) >= 1:
            # Utterance contained at least one proper command
            for cmd in conceptCmds:
                string = cmd.getAMANString(mode, getConfidence, getSubconfidences)
                outputs.append(string)
        else:
            # Utterance contained no proper command. Looking for unimportant/incomplete commands
            frames = dict(callsign=self.callsign.getFrames(),
                          concept=list(),
                          value=list(),
                          metric=list())

            if len(noConceptCmds) >= 1:  # There is at least one unimportant or incomplete command
                for cmd in noConceptCmds:
                    if cmd.conceptFrame is not None:
                        frames['concept'].append(cmd.conceptFrame)
                    if cmd.valueFrame is not None:
                        frames['value'].append(cmd.valueFrame)
                    if cmd.metricFrame is not None:
                        frames['metric'].append(cmd.metricFrame)

            else:  # There are no commands, assume everything was noise
                return []

            # Prepare string construction
            cmd = [self.callsign.callsign, NO_CONCEPT]
            frames['total'] = itertools.chain.from_iterable(list(frames.values()))

            # Compute confidences
            items = list()
            if getSubconfidences:
                items.extend(['callsign', 'concept', 'value'])
            if getConfidence:
                items.append('total')
            for item in items:
                score = TagFrame.getJointConfidenceScore(frames[item], mode, contentOnly=contentOnly)
                if score is not None:
                    score = round(score, PRECISION)
                    cmd.append(str(score))

            outputs.append(' '.join(cmd))

        return outputs


class ConceptGenerator:
    """
    Concept generator sets up information that is persistent across multiple
    utterances to avoid redundancy in loading files.
    """

    def __init__(self, airlineFile, contextFile=None):
        absolutepath = getAbsolutePath()

        airlineFile = path.join(absolutepath, airlineFile)
        self.airlineShorts = loadAirlineCallsigns(airlineFile)

        if contextFile is None:
            self.contextFile = None
        else:
            self.contextFile = path.join(absolutepath, contextFile)

    def recognize(self, sentenceFrame, confidenceMode=CONFMODE_OFF, isStrict=False):
        commandSet = CommandSet(sentenceFrame, self.airlineShorts, self.contextFile, isStrict=isStrict)
        return commandSet.getAMANStrings(confidenceMode, getConfidence=True, getSubconfidences=True)

    def recognizeString(self, string, confidenceMode=CONFMODE_OFF, isStrict=False):
        sentenceFrame = TagFrame.loadFromString(string)
        return self.recognize(sentenceFrame, confidenceMode=confidenceMode, isStrict=isStrict)

    def extractCommand(self, string, isStrict=False):
        sentenceFrame = TagFrame.loadFromString(string)
        return CommandSet(sentenceFrame, self.airlineShorts, self.contextFile, isStrict=isStrict)


def recognize(sentenceFrame, airlineFile, contextFile=None, confidenceMode=CONFMODE_OFF, isStrict=False):
    """
    Convert an xml-enhanced sentence into aman concepts.
    Returns a list of commands, one command per line.
    The input tokens must be a list of (token, confidence) pairs.
    When providing airline_file or context_file, beware that the file's path is
    relative to the called script (i.e. sys.argv[0]), not pwd.
    This usually means you have to move back out of tools/ via ../
    """
    generator = ConceptGenerator(airlineFile, contextFile=contextFile)
    return generator.recognize(sentenceFrame, confidenceMode=confidenceMode, isStrict=isStrict)


def recognizeString(string, airlineFile, contextFile=None, confidenceMode=CONFMODE_OFF, isStrict=False):
    sentenceFrame = TagFrame.loadFromString(string)
    return recognize(sentenceFrame, airlineFile=airlineFile, contextFile=contextFile, confidenceMode=confidenceMode,
                     isStrict=isStrict)
