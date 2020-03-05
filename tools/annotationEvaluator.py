"""
Evaluates the discrepancies between gold annotation and automatically generated
content (i.e. what recognizer would be capable of).
Will recursively evaluate all sub-dirs too.
Currently only compares cmd files, not tra files.
Will only compare cmd files for which tra files are available.

@author: Marc Schulder
"""
import os
import re
from os import path
from FileTools import stripXML


def textsEqual(text1, text2):
    """
    Compare texts, disregarding pause markers.
    Return true if texts are identical except for disregarded things.
    """
    if text1 is None or text2 is None:
        if text1 is None and text2 is None:
            return True
        else:
            return False
    text1 = text1.replace('__pause__', '')
    text2 = text2.replace('__pause__', '')
    text1 = text1.strip()
    text2 = text2.strip()
    while '  ' in text1:
        text1 = text1.replace('  ', ' ')
    while '  ' in text2:
        text2 = text2.replace('  ', ' ')
    return text1 == text2


class Utterance:
    def __init__(self, dirpath, fileroot):
        self.dirpath = dirpath
        self.root = fileroot
        self.absolute_root = path.join(dirpath, fileroot)
        self.extensions = set()
        self.cor = None
        self.tra = None
        self.cmd = None
        self.genCmd = None
        self.cmdCallsign = None
        self.genCallsign = None
        self.cmdConcepts = None
        self.genConcepts = None
        self.cmdTypes = None
        self.genTypes = None

    def _loadFile_(self, extension, makeList=False):
        absolute_root = path.join(self.dirpath, self.root)
        with open("{0}.{1}".format(absolute_root, extension)) as f:
            if makeList:
                content = [line.strip() for line in f.readlines()]
            else:
                content = f.read().strip()
        return content

    def addExtension(self, extension):
        self.extensions.add(extension)

    def has(self, extension):
        return extension in self.extensions

    def getCor(self):
        if not self.has('cor'):
            return None
        elif self.cor is None:
            self.cor = self._loadFile_('cor', makeList=False)
        return self.cor

    def getTra(self):
        if not self.has('tra'):
            return None
        elif self.tra is None:
            self.tra = self._loadFile_('tra', makeList=False)
        return self.tra

    def getCmd(self):
        if not self.has('cmd'):
            return None
        elif self.cmd is None:
            self.cmd = self._loadFile_('cmd', makeList=True)
            self.cmdCallsign, self.cmdConcepts = self._parseCmds_(self.cmd)
            self.cmdTypes = self._extractCmdTypes_(self.cmdConcepts)
        return self.cmd

    def getCmdCallsign(self):
        if self.cmdCallsign is None:
            self.getCmd()
        return self.cmdCallsign

    def getCmdConcepts(self):
        if self.cmdConcepts is None:
            self.getCmd()
        return self.cmdConcepts

    def getCmdTypes(self):
        if self.cmdTypes is None:
            self.getCmd()
        return self.cmdTypes

    def generateCmd(self, generator):
        if self.genCmd is None:  # Commands not generated yet
            tra = self.getTra()
            if tra is None or generator is None:
                return None
            else:
                self.genCmd = generator.recognizeString(tra)
                self.genCallsign, self.genConcepts = self._parseCmds_(self.genCmd)
                self.genTypes = self._extractCmdTypes_(self.genConcepts)
        return self.genCmd

    def getGeneratedCallsign(self, generator):
        if self.genCallsign is None:
            self.generateCmd(generator)
        return self.genCallsign

    def getGeneratedConcepts(self, generator):
        if self.genConcepts is None:
            self.generateCmd(generator)
        return self.genConcepts

    def getGeneratedTypes(self, generator):
        if self.genTypes is None:
            self.generateCmd(generator)
        return self.genTypes

    def _parseCmds_(self, cmds):
        callsign = None
        cpts = []
        for cmd in cmds:
            parts = cmd.split(' ', 1)
            if len(parts) == 1:
                print "WARNING: Cmd is missing either callsign or command:", self.absolute_root
                cpts.append(parts[0])
            else:
                cpts.append(parts[1])
                if callsign is None:
                    callsign = parts[0]
                elif callsign != parts[0]:
                    print "WARNING: Different callsigns in commands of single utterance found:", self.absolute_root
        return callsign, cpts

    @staticmethod
    def _extractCmdTypes_(cpts):
        types = set()
        for cpt in cpts:
            parts = cpt.split(' ', 1)
            types.add(parts[0])
        return types

    def xmlIsInferrable(self, xmlCommands, keywords, cmdMustBeInAnnotation=False):
        """
        Returns True if command can be inferred from text, irrespective of xml annotation
        In the case where the given commandType can't be found in the xml tags,
        True is returned, unless cmdMustBeInAnnotation is set to True.
        xmlCommands and keywords are both iterables containing the
        xml command tags and text keywords respectively.
        Both are lists of alternatives, so only one of them has to match.
        """
        re_commands = re.findall('(<command=\"[a-z_]+\">.+?</command>)', self.getTra())
        if len(re_commands) >= 1:
            matchedCommand = False
            for full_command in re_commands:
                re_command = re.match('<command=\"([a-z_]+)\">(.+?)</command', full_command)
                command_type = re_command.group(1)
                command_content = re_command.group(2)
                for xmlCommand in xmlCommands:
                    matchedCommand = True
                    if command_type == xmlCommand:
                        for cmdKeyword in keywords:
                            if cmdKeyword not in command_content:
                                return False
            if matchedCommand:
                return True
        # In case no command was found
        if cmdMustBeInAnnotation:
            return False
        else:
            return True

    def commandIsInferrable(self, concepts, keywords):
        matchedConcept = False
        for concept in concepts:
            concept = concept.upper()
            if concept in self.getCmdTypes():
                matchedConcept = True
                for keyword in keywords:
                    if keyword in self.getCor():
                        return True
        return not matchedConcept

    def listIncorrectValues(self, generator):
        incorrectValues = []
        for gen in self.getGeneratedConcepts(generator):
            genParts = gen.split(' ', 1)
            for cmd in self.getCmdConcepts():
                cmdParts = cmd.split(' ', 1)
                if genParts[0] == cmdParts[0]:
                    if len(genParts) == 1:
                        genParts.append('')
                    if len(cmdParts) == 1:
                        cmdParts.append('')
                    if genParts[1] != cmdParts[1]:
                        incorrectValues.append(genParts)
        return incorrectValues


class DirStats:
    def __init__(self):
        self.total = 0
        self.dirCounts = dict()
        self.dirItems = dict()
        self.items = set()

    def addItem(self, dirname, item):
        self.total += 1
        if dirname not in self.dirCounts:
            self.dirCounts[dirname] = 0
        self.dirCounts[dirname] += 1
        self.dirItems.setdefault(dirname, set()).add(item)
        self.items.add(item)

    def getTotal(self):
        return self.total

    def getMaximumDirCount(self):
        return max(self.dirCounts.values())

    def getDirCount(self, dirname, allowUnknownDir=True):
        if allowUnknownDir:
            return self.dirCounts.get(dirname, 0)
        else:
            return self.dirCounts.get(dirname, None)

    def getDirItems(self, dirname):
        return self.dirItems.get(dirname, None)

    def dirItemIterator(self):
        return self.dirItems.iteritems()

    def printStatList(self, countername):
        print 'Total of {0} {1}'.format(self.total, countername)
        if self.getTotal() > 0:
            for dirpath, utterances in self.dirItemIterator():
                numUtterances = len(utterances)
                if numUtterances > 0:
                    print "{1} {2} in {0}:".format(dirpath, numUtterances, countername)
                    for utterance in utterances:
                        print "   {0.root}".format(utterance)

    def printCmdList(self, countername):
        print 'Total of {0} {1}'.format(self.total, countername)
        if self.getTotal() > 0:
            for dirpath, utterances in self.dirItemIterator():
                numUtterances = len(utterances)
                if numUtterances > 0:
                    print "{1} {2} in {0}:".format(dirpath, numUtterances, countername)
                    for utterance in utterances:
                        print "   {0.root}   {0.genCmd} instead of {0.cmd}".format(utterance)

    def printStatDetails(self):
        showedInstance = False
        for utterance in self.items:
            showedInstance = True
            print "File:              ", utterance.absolute_root
            print "Transcription:     ", utterance.getTra()
            print "Gold commands:     ", utterance.getCmd()
            print "Generated commands:", utterance.generateCmd(None)
            print
        if not showedInstance:
            print "- None -"


class CommandEvaluator:
    def __init__(self, conceptGenerator):
        self.generator = conceptGenerator

        self.utteranceDirs = None
        self.error = None
        self.mismatch = None
        self.mismatchCorTra = None
        self.missingCallsigns = None
        self.incompleteCallsigns = None
        self.incorrectCallsigns = None
        self.mismatchMisc = None
        self.falseReduceTag = None
        self.reduceCmdInferred = None
        self.aboveBelowUntagged = None
        self.turnNoDir = None
        self.ilsMismatch = None
        self.incorrectValue = None
        self.deprecatedTag = None
        self.missingTra = None
        self.missingCmd = None

        self.reset()

    def reset(self):
        self.utteranceDirs = []
        self.error = DirStats()
        self.mismatch = DirStats()
        self.mismatchCorTra = DirStats()

        self.missingCallsigns = DirStats()
        self.incompleteCallsigns = DirStats()
        self.incorrectCallsigns = DirStats()

        self.mismatchMisc = DirStats()
        self.falseReduceTag = DirStats()
        self.reduceCmdInferred = DirStats()
        self.aboveBelowUntagged = DirStats()
        self.turnNoDir = DirStats()
        self.ilsMismatch = DirStats()

        self.incorrectValue = DirStats()

        self.deprecatedTag = DirStats()

        self.missingTra = DirStats()
        self.missingCmd = DirStats()

    def evaluate(self, dirContents, verbose=True):
        relevantDirs = set()
        for dirpath, utterances in dirContents.iteritems():
            if len(utterances) > 0:
                self._evaluateDir_(dirpath, utterances)
                relevantDirs.add(dirpath)
        self.utteranceDirs = sorted(relevantDirs)

        if verbose:
            print "=== Miscellaneous mismatches between cmd annotation and commands generated from tra annotation ==="
            self.mismatchMisc.printStatDetails()

            print "=== Mismatches between text in cor and tra files ==="
            self.mismatchCorTra.printStatDetails()
            #
            print '\n=== TURN_HEADING command without a mentioned direction ==='
            self.turnNoDir.printStatDetails()

            print '\n=== REDUCE command without word "reduce" ==='
            self.reduceCmdInferred.printStatDetails()

            print '\n=== Above/Below word not tagged ==='
            self.aboveBelowUntagged.printStatDetails()

            print '\n=== ILS left/right mismatch ==='
            self.ilsMismatch.printCmdList('ILS left/right mismatches')

            print '\n=== Used "reduce" tag where only "speed" can be known ==='
            self.falseReduceTag.printCmdList('reduce tag for speed tag')

            print '\n=== Incorrect values ==='
            self.incorrectValue.printStatDetails()

            print '\n=== Deprecated Tags ==='
            self.deprecatedTag.printStatDetails()

            print "\n=== Missing Callsigns (but inferred by annotator) ==="
            self.missingCallsigns.printCmdList('missing callsigns')

            print "\n=== Incomplete Callsigns (but inferred by annotator) ==="
            self.missingCallsigns.printCmdList('incomplete callsigns')

            print "\n=== Incorrect Callsigns ==="
            self.missingCallsigns.printCmdList('incorrect callsigns')

            print "\n=== Missing xml annotations (i.e. tra files) ==="
            self.missingTra.printStatList('missing tra')

            print "\n=== Missing cmd annotations (i.e. cmd files) ==="
            self.missingCmd.printStatList('missing cmd')

            print ''

    def _evaluateDir_(self, dirpath, utterances):
        for utterance in sorted(utterances.itervalues()):
            if utterance.has('tra'):
                deprecatedTags = ['fix']
                for deprecatedTag in deprecatedTags:
                    if '<%s>' % deprecatedTag in utterance.getTra():
                        self.deprecatedTag.addItem(dirpath, utterance)
                deprecatedCommands = ['descent_rate',
                                      'cleared',
                                      'heading_command',
                                      'contact',
                                      'turn_cleared',
                                      'speed_boundary',
                                      'maintain_level',
                                      'level_cleared']
                for deprecatedCommand in deprecatedCommands:
                    if '<command="%s">' % deprecatedCommand in utterance.getTra():
                        self.deprecatedTag.addItem(dirpath, utterance)

            if utterance.has('cor') and utterance.has('tra'):
                tra = utterance.getTra()
                cor = utterance.getCor()
                tra = stripXML(tra)
                while '  ' in tra:
                    tra = tra.replace('  ', ' ')
                while '  ' in cor:
                    cor = cor.replace('  ', ' ')
                if cor != tra:
                    self.mismatchCorTra.addItem(dirpath, utterance)
                    self.error.addItem(dirpath, utterance)

            if utterance.has('tra') and utterance.has('cmd'):
                cmds_gold = utterance.getCmd()
                cmds_hyp = utterance.generateCmd(self.generator)
                if not self.isEqualList(cmds_gold, cmds_hyp):
                    # Inspection of mismatched callsigns
                    cmdCS = utterance.getCmdCallsign(self.generator)
                    genCS = utterance.getGeneratedCallsign(self.generator)
                    if not textsEqual(genCS, cmdCS):
                        self.mismatch.addItem(dirpath, utterance)
                        self.error.addItem(dirpath, utterance)
                        if genCS == 'NO_CALLSIGN':
                            self.missingCallsigns.addItem(dirpath, utterance)
                        elif genCS.startswith('NO_AIRLINE_'):
                            self.incompleteCallsigns.addItem(dirpath, utterance)
                        elif genCS.endswith('_NO_FLIGHTNUMBER'):
                            self.incompleteCallsigns.addItem(dirpath, utterance)
                        else:
                            self.incorrectCallsigns.addItem(dirpath, utterance)

                    # Inspection of mismatched command concepts
                    cmdCpts = utterance.getCmdConcepts(self.generator)
                    genCpts = utterance.getGeneratedConcepts(self.generator)
                    if not self.isEqualList(cmdCpts, genCpts):
                        self.mismatch.addItem(dirpath, utterance)
                        self.error.addItem(dirpath, utterance)
                        isSpecial = False
                        if not utterance.xmlIsInferrable(['reduce'], ['reduce']):
                            self.falseReduceTag.addItem(dirpath, utterance)
                            isSpecial = True
                        commands = ['REDUCE_OR_BELOW', 'REDUCE_NOT_BELOW',
                                    'INCREASE_OR_ABOVE', 'INCREASE_NOT_ABOVE',
                                    'SPEED_OR_ABOVE', 'SPEED_OR_BELOW',
                                    'DESCEND_OR_BELOW', 'DESCEND_NOT_BELOW',
                                    'RATE_OF_DESCENT_OR_ABOVE', 'RATE_OF_DESCENT_NOT_ABOVE',
                                    'CLIMB_OR_ABOVE', 'CLIMB_NOT_ABOVE',
                                    'RATE_OF_CLIMB_OR_BELOW', 'RATE_OF_CLIMB_NOT_BELOW',
                                    'ALTITUDE_OR_ABOVE', 'ALTITUDE_OR_BELOW']
                        keywords = ['<more>', '<less>', '<greater>', '<above>', '<below>']
                        if not utterance.commandIsInferrable(commands, keywords):
                            self.aboveBelowUntagged.addItem(dirpath, utterance)
                            isSpecial = True
                        if not utterance.commandIsInferrable(['REDUCE', 'REDUCE_OR_BELOW', 'REDUCE_NOT_BELOW'],
                                                             ['reduce']):
                            self.reduceCmdInferred.addItem(dirpath, utterance)
                            isSpecial = True
                        if not utterance.commandIsInferrable(['TURN_LEFT_HEADING'],
                                                             ['left']) or not utterance.commandIsInferrable(
                                ['TURN_RIGHT_HEADING'], ['right']):
                            self.turnNoDir.addItem(dirpath, utterance)
                            isSpecial = True

                        incorrectValues = utterance.listIncorrectValues(self.generator)
                        for cpt, val in incorrectValues:
                            self.incorrectValue.addItem(dirpath, utterance)
                            isSpecial = True
                            if cpt in ["CLEARED_ILS", "CLEARED"]:
                                pass  # Todo

                        if not isSpecial:
                            self.mismatchMisc.addItem(dirpath, utterance)
            elif utterance.has('tra'):
                # Has tra, but no cmd
                cmds_hyp = utterance.generateCmd(self.generator)
                if cmds_hyp != 'NO_CALLSIGN NO_CONCEPT':
                    self.missingCmd.addItem(dirpath, utterance)
                    self.error.addItem(dirpath, utterance)

            elif utterance.has('cmd'):
                # Has cmd, but no tra
                self.missingTra.addItem(dirpath, utterance)
                self.error.addItem(dirpath, utterance)
            else:
                # Has neither cmd, nor tra
                pass

    @staticmethod
    def isEqualList(cmds_gold, cmds_hyp):
        is_equal = True
        if cmds_gold is None or cmds_hyp is None:
            is_equal = cmds_gold == cmds_hyp
        elif len(cmds_gold) != len(cmds_hyp):
            is_equal = False
        else:
            for i in range(len(cmds_gold)):
                is_equal = is_equal and textsEqual(cmds_gold[i], cmds_hyp[i])
        return is_equal

    def summarize(self):
        summary_callsigns = '   Callsign: {0} missing callsigns, {1} incomplete callsigns,  {2} incorrect callsigns'
        summary_commands = '   Command:  {0} REDUCE without "reduce", {1} TURN without directions'
        summary_values = '   Value:    {0} commands with incorrect values'
        summary_xml = '   XML:      {0} "reduce" tag instead of "speed" tag, {1} above/below word not tagged, ' \
                      '{2} deprecated tags (not counted as errors)'
        summary_missing = '   Missing:  {0} missing tra, {1} missing cmd'
        summary_misc = '   Misc:     {0} unclassified command mismatches'
        print '=== Summary of Command Generation ==='

        # Summary by directory
        for dirname in self.utteranceDirs:
            print '{1} errors (in {2} files in {0})'.format(dirname, self.error.getDirCount(dirname),
                                                            len(self.error.getDirItems(dirname)))
            print summary_callsigns.format(self.missingCallsigns.getDirCount(dirname),
                                           self.incompleteCallsigns.getDirCount(dirname),
                                           self.incorrectCallsigns.getDirCount(dirname))
            print summary_commands.format(self.reduceCmdInferred.getDirCount(dirname),
                                          self.turnNoDir.getDirCount(dirname))
            print summary_values.format(self.incorrectValue.getDirCount(dirname))
            print summary_xml.format(self.falseReduceTag.getDirCount(dirname),
                                     self.aboveBelowUntagged.getDirCount(dirname),
                                     self.deprecatedTag.getDirCount(dirname))
            print summary_missing.format(self.missingTra.getDirCount(dirname),
                                         self.missingCmd.getDirCount(dirname))
            print summary_misc.format(self.mismatchMisc.getDirCount(dirname))

        # Overall summary
        print '\nTotal: {0} errors (in {1} files)'.format(self.error.total, len(self.error.items))
        print summary_callsigns.format(self.missingCallsigns.total, self.incompleteCallsigns.total,
                                       self.incorrectCallsigns.total)
        print summary_commands.format(self.reduceCmdInferred.total, self.turnNoDir.total)
        print summary_values.format(self.incorrectValue.total)
        print summary_xml.format(self.falseReduceTag.total, self.aboveBelowUntagged.total,
                                 self.deprecatedTag.total)
        print summary_missing.format(self.missingTra.total, self.missingCmd.total)
        print summary_misc.format(self.mismatchMisc.total)


def prepareDirInfos(filedir, recurse=True):
    dirContents = dict()
    # Top down search through directory tree
    for dirpath, dirnames, filenames in os.walk(filedir, topdown=True):
        # Restrict search to visible directories
        for i, subdir in enumerate(dirnames[:]):
            if subdir.startswith('.'):
                del dirnames[i]
        # Prepare file info in this dir
        if len(filenames) > 0:
            utterances = dict()
            for filename in filenames:
                root, ext = path.splitext(filename)
                if len(ext) > 0:
                    utterance = utterances.setdefault(root, Utterance(dirpath, root))
                    utterance.addExtension(ext[1:])
            dirContents[dirpath] = utterances
        if not recurse:
            break
    return dirContents


def listErroneousUtterances(filedir, conceptGenerator):
    dirContents = prepareDirInfos(filedir, recurse=False)
    cmdEval = CommandEvaluator(conceptGenerator)
    cmdEval.evaluate(dirContents, verbose=False)
    dirItems = cmdEval.error.dirItems
    if len(dirItems) > 1:
        print "Received more than one directory. This should have been impossible!"
    for utterances in dirItems.itervalues():
        return utterances
    return []
