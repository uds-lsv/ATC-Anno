"""
Annotation tool for air traffic control recordings.
@author: Marc Schulder
@author: Yuri Bakanouski
"""
import array
import atexit
import os
import re
import struct
import sys
import wave
from glob import iglob
from os import path
from subprocess import Popen

import argparse
import logging
import pyaudio
import shutil
import time
import wx
import wx.stc as stc
from threading import Thread

from tools.GenerateConcept import ConceptGenerator
from tools.Text2XML import Text2XMLConverter
from tools.annotationEvaluator import listErroneousUtterances
from tools.FileTools import stripXML, alphanum_key

DEFAULT_AIRLINE_FILE = 'data/airlines/callsigns.txt'
DEFAULT_OPENFST_GRAMMAR_FILE = 'data/grammars/default.fst'

WAV_EXT = '.wav'
HYP_EXT = '.txt'
CMD_HYP_EXT = '.cpt'
TEXT_ANNO_EXT = '.cor'
XML_ANNO_EXT = '.tra'
CMD_ANNO_EXT = '.cmd'
COMMENT_EXT = '.nfo'

BUTTON_GENERATE = 0
BUTTON_ABORT = 1
BUTTON_REVERT = 2


class MyListCtrl(wx.ListCtrl):
    def __init__(self, parent, idnum, conceptGenerator):
        wx.ListCtrl.__init__(self, parent, idnum, pos=(10, 50), size=(300, 290), style=wx.LC_REPORT | wx.BORDER_SUNKEN)

        self.conceptGenerator = conceptGenerator

        self.InsertColumn(0, 'Name')
        self.SetColumnWidth(0, 290)

        self.fileIDs = dict()
        self.textAnnoFiles = set()
        self.xmlAnnoFiles = set()
        self.cmdAnnoFiles = set()

    def update(self, workDir, filterFiles):
        self.fileIDs = dict()
        files = []
        self.textAnnoFiles = set()
        self.xmlAnnoFiles = set()
        self.cmdAnnoFiles = set()
        fileSets = {TEXT_ANNO_EXT: self.textAnnoFiles,
                    XML_ANNO_EXT: self.xmlAnnoFiles,
                    CMD_ANNO_EXT: self.cmdAnnoFiles,
                    }

        # Update play list
        if filterFiles:
            for utterance in set(listErroneousUtterances(workDir, self.conceptGenerator)):
                for ext in utterance.extensions:
                    ext = '.' + ext
                    if ext == WAV_EXT:
                        files.append(utterance.root + ext)
                    elif ext in fileSets:
                        fileSets[ext].add(utterance.root)
        else:
            all_files = os.listdir(workDir)
            for filename in all_files:
                (root, ext) = path.splitext(filename)
                if ext == WAV_EXT:
                    files.append(filename)
                elif ext in fileSets:
                    fileSets[ext].add(root)

        self.DeleteAllItems()
        sorted_files = sorted(files, key=alphanum_key, reverse=False)
        for i, filename in enumerate(sorted_files):
            (root, ext) = path.splitext(filename)
            self.fileIDs[root] = i
            self.InsertItem(i, "{}. {}".format(i + 1, root))
            self.updateAnnotationStatus(root)

    def setAnnotation(self, filename, fileList, exists=True):
        if exists and filename not in fileList:
            fileList.add(filename)
            self.updateAnnotationStatus(filename)
        elif not exists and filename in fileList:
            fileList.remove(filename)
            self.updateAnnotationStatus(filename)

    def setTextAnno(self, filename, exists=True):
        self.setAnnotation(filename, self.textAnnoFiles, exists)

    def setXMLAnno(self, filename, exists=True):
        self.setAnnotation(filename, self.xmlAnnoFiles, exists)

    def setCommandAnno(self, filename, exists=True):
        self.setAnnotation(filename, self.cmdAnnoFiles, exists)

    def updateAnnotationStatus(self, filename):
        if filename in self.fileIDs:
            i = self.fileIDs[filename]
            if filename in self.xmlAnnoFiles:
                self.SetItemTextColour(i, 'black')
            elif filename in self.textAnnoFiles:
                self.SetItemTextColour(i, 'blue')
            else:
                self.SetItemTextColour(i, 'red')
        else:
            print "WARNING: %s does not exist" % filename


class WaveformPanel(wx.Window):
    def __init__(self, parent, x, y, width, height):
        self.width = width
        self.height = height
        pos = wx.Point(x, y)
        size = wx.Size(width, height)
        wx.Window.__init__(self, parent, wx.ID_ANY, pos, size, wx.BORDER_STATIC | wx.SUNKEN_BORDER)
        self.Bind(event=wx.EVT_PAINT, handler=self.OnPaint)
        self._Buffer = wx.Bitmap(width, height)
        self.UpdateWaveform()

    def OnPaint(self, _):
        # All that is needed here is to draw the buffer to screen
        wx.BufferedPaintDC(self, self._Buffer)

    def UpdateWaveform(self, data=None):
        dc = wx.MemoryDC()
        dc.SelectObject(self._Buffer)
        self.Draw(dc, data)
        del dc  # need to get rid of the MemoryDC before Update() is called.
        self.Refresh(eraseBackground=False)
        self.Update()

    def Draw(self, dc, data):
        dc.SetBackground(wx.Brush("White"))
        dc.Clear()  # make sure you clear the bitmap!

        myPen = wx.Pen(wx.Colour(0, 255, 0), width=1, style=wx.SOLID)
        dc.SetPen(myPen)

        if data is not None:
            # read wav data into array
            nSamples = len(data)
            absmax = max(abs(min(data)), abs(max(data)))
            xscale = (float(self.width)) / nSamples
            yscale = 0.48 * (float(self.height) - 1.0) / absmax + 1E-10

            lastX = 0
            minY = 0.5 * self.height
            maxY = 0.5 * self.height
            for i in range(nSamples):
                x = int(xscale * i)
                y = int(round(float(data[i]) * yscale)) + 0.5 * self.height
                if y < minY:
                    minY = y
                if y > maxY:
                    maxY = y
                if x != lastX:
                    dc.DrawLine(lastX, minY, lastX, maxY)
                    lastX = x
                    minY = y
                    maxY = y


if wx.Platform == '__WXMSW__':  # Fonts for running the tool on Windows
    faces = {'times': 'Times New Roman',
             'mono': 'Courier New',
             'helv': 'Helvetica',
             'other': 'Courier New',
             'size': 11,
             'size2': 9,
             }
elif wx.Platform == '__WXMAC__':  # Fonts for running the tool on Mac OSX
    faces = {'times': 'Times New Roman',
             'mono': 'Monaco',
             'helv': 'Helvetica',
             'other': 'Courier New',
             'size': 11,
             'size2': 9,
             }
else:  # Fonts for running the tool on everything else (i.e. Linux)
    faces = {'times': 'Times',
             'mono': 'Courier',
             'helv': 'Helvetica',
             'other': 'new century schoolbook',
             'size': 11,
             'size2': 9,
             }

specialTagsXML = ['command', 'callsign', 'airline', 'flightnumber']
tagsXML = ['s', 'commands',
           'waypoint', 'contact', 'degree_absolute', 'degree_relative', 'speed',
           'flightlevel', 'altitude', 'feet_per_minute', 'direction', 'distance',
           'more', 'less', 'above', 'below', 'neg', 'runway', 'qnh_value', 'frequency',
           'information', 'holdpoint']
cmdsXML = {'cleared_ils': ['runway'],
           'descend': ['flightlevel', 'altitude'],
           'expect_ils': ['runway'],
           'direct_to': ['direction', 'waypoint'],
           'give_altitude': ['flightlevel', 'altitude'],
           'give_speed': ['speed', 'more', 'less', 'distance'],
           'handover': ['contact', 'frequency'],
           'heading': ['degree_absolute'],
           'increase': ['speed', 'more', 'less', 'distance'],
           'information': ['information'],
           'init_response': [],
           'maintain_altitude': ['flightlevel', 'altitude'],
           'maintain_speed': ['speed', 'more', 'less', 'distance'],
           'rate_of_descent': ['feet_per_minute', 'more', 'less'],
           'reduce': ['speed', 'more', 'less', 'distance'],
           'reduce_final_app': [],
           'reduce_min_clean': [],
           'report_established': [],
           'report_speed': [],
           'speed_own': [],
           'transition': ['waypoint', 'degree_absolute'],
           'touchdown': ['distance'],
           'turn': ['direction', 'degree_relative'],
           'turn_heading': ['direction', 'degree_absolute'],
           'vector': ['runway'],
           'qnh': ['qnh_value'],
           'cleared_ndb': ['runway'],
           'cleared_rnav': ['runway'],
           'intercept_localizer': ['runway'],
           'expect_runway': ['runway'],
           'climb': ['flightlevel', 'altitude'],
           'rate_of_descent_own': [],
           'rate_of_climb': ['feet_per_minute', 'more', 'less'],
           'rate_of_climb_own': [],
           'holding': ['holdpoint', 'flightlevel'],
           'leave_holding': ['holdpoint'],
           'go_around': [],
           'navigation_own': [],
           }
keywordsXml = tagsXML + specialTagsXML + cmdsXML.keys()
autocompXML = ['<callsign><airline></airline><flightnumber></flightnumber></callsign>']
for tag in tagsXML:
    autocompXML.append('<{0}>  </{0}>'.format(tag))

for cmd, content in cmdsXML.iteritems():
    elems = ['<command="{0}"> '.format(cmd)]
    for tag in content:
        if len(tag.strip()) > 0:
            elems.append('<{0}>  </{0}>'.format(tag))
    elems.append(' </command>')
    autocompXML.append(''.join(elems))
autocompXML = sorted(autocompXML)


class XMLText(stc.StyledTextCtrl):
    fold_symbols = 2

    def __init__(self, parent, ID,
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=0, allowAutoComp=True):
        stc.StyledTextCtrl.__init__(self, parent, ID, pos, size, style)

        self.allowAutoComp = allowAutoComp

        self.CmdKeyAssign(ord('B'), stc.STC_SCMOD_CTRL, stc.STC_CMD_ZOOMIN)
        self.CmdKeyAssign(ord('N'), stc.STC_SCMOD_CTRL, stc.STC_CMD_ZOOMOUT)

        self.AutoCompSetSeparator(ord(';'))
        # Python sorts are case sensitive so this needs to match
        self.AutoCompSetIgnoreCase(False)

        self.SetLexer(stc.STC_LEX_XML)
        self.SetKeyWords(0, " ".join(keywordsXml))

        self.SetProperty("fold", "1")
        self.SetProperty("tab.timmy.whinge.level", "1")
        self.SetMargins(0, 0)
        self.SetMarginWidth(1, 0)
        self.SetWrapMode(stc.STC_WRAP_WORD)

        self.SetViewWhiteSpace(False)
        self.Bind(stc.EVT_STC_UPDATEUI, self.OnUpdateUI)
        self.Bind(wx.EVT_KEY_DOWN, self.OnKeyPressed)

        self.SetStyle()

    def SetBackgroundColour(self, colour):
        self.StyleSetBackground(stc.STC_STYLE_DEFAULT, colour)
        self.SetStyle()

    def SetStyle(self, start=None, end=None, style=None):
        """
        Make some styles,  The lexer defines what each style is used for, we
        just have to define what each style looks like.  This set is adapted from
        Scintilla sample property files.
        """
        # Global default styles for all languages
        self.StyleSetSpec(stc.STC_STYLE_DEFAULT, "face:%(helv)s,size:%(size)d" % faces)
        self.StyleClearAll()  # Reset all to be like the default

        # Global default styles for all languages
        self.StyleSetSpec(stc.STC_STYLE_DEFAULT, "face:%(helv)s,size:%(size)d" % faces)
        self.StyleSetSpec(stc.STC_STYLE_LINENUMBER, "back:#C0C0C0,face:%(helv)s,size:%(size2)d" % faces)
        self.StyleSetSpec(stc.STC_STYLE_CONTROLCHAR, "face:%(other)s" % faces)
        self.StyleSetSpec(stc.STC_STYLE_BRACELIGHT, "fore:#FFFFFF,back:#0000FF,bold")
        self.StyleSetSpec(stc.STC_STYLE_BRACEBAD, "fore:#000000,back:#FF0000,bold")

        # Python styles
        # Default
        self.StyleSetSpec(stc.STC_P_DEFAULT, "fore:#000000,face:%(helv)s,size:%(size)d" % faces)
        # Comments
        self.StyleSetSpec(stc.STC_P_COMMENTLINE, "fore:#007F00,face:%(other)s,size:%(size)d" % faces)
        # Number
        self.StyleSetSpec(stc.STC_P_NUMBER, "fore:#007F7F,size:%(size)d" % faces)
        # String
        self.StyleSetSpec(stc.STC_P_STRING, "fore:#7F007F")
        # Single quoted string
        self.StyleSetSpec(stc.STC_P_CHARACTER, "fore:#7F007F,face:%(helv)s,size:%(size)d" % faces)
        # Keyword
        self.StyleSetSpec(stc.STC_P_WORD, "fore:#00007F,bold,size:%(size)d" % faces)
        # Triple quotes
        self.StyleSetSpec(stc.STC_P_TRIPLE, "fore:#7F0000,size:%(size)d" % faces)
        # Triple double quotes
        self.StyleSetSpec(stc.STC_P_TRIPLEDOUBLE, "fore:#7F0000,size:%(size)d" % faces)
        # Class name definition
        self.StyleSetSpec(stc.STC_P_CLASSNAME, "fore:#0000FF,bold,size:%(size)d" % faces)
        # Function or method name definition
        self.StyleSetSpec(stc.STC_P_DEFNAME, "fore:#007F7F,bold,size:%(size)d" % faces)
        # Operators
        self.StyleSetSpec(stc.STC_P_OPERATOR, "bold,size:%(size)d" % faces)
        # Identifiers
        self.StyleSetSpec(stc.STC_P_IDENTIFIER, "fore:#000000,face:%(helv)s,size:%(size)d" % faces)
        # Comment-blocks
        self.StyleSetSpec(stc.STC_P_COMMENTBLOCK, "fore:#7F7F7F,size:%(size)d" % faces)
        # End of line where string is not closed
        self.StyleSetSpec(stc.STC_P_STRINGEOL, "fore:#000000,face:%(mono)s,back:#E0C0E0,eol,size:%(size)d" % faces)

        self.SetCaretForeground("BLUE")

    def SetValue(self, text):
        readOnly = self.GetReadOnly()
        if readOnly:
            self.SetReadOnly(readOnly=False)
        output = self.SetText(text)
        if readOnly:
            self.SetReadOnly(readOnly=True)
        return output

    def GetValue(self):
        return self.GetText()

    def SetEditable(self, isEditable):
        self.SetReadOnly(readOnly=(not isEditable))

    def OnKeyPressed(self, event):
        if self.CallTipActive():
            self.CallTipCancel()
        key = event.GetKeyCode()

        if not self.allowAutoComp:
            event.Skip()
        elif key == ord('<'):
            # Start autocomplete when tag is opened
            self.AutoCompShow(0, ";".join(autocompXML))
            event.Skip()  # ensures that < is entered in text box
        elif key == ord(' ') and event.ControlDown():
            # Manually call autocomplete by pressing ctrl+space
            # Find where tag started
            pos = self.GetCurrentPos()
            chars = []
            while pos > 0:
                pos -= 1
                char = chr(self.GetCharAt(pos))
                if char in ['>', ' ', '\n']:
                    break
                else:
                    chars.append(char)
            # Code completion
            self.AutoCompShow(len(chars), ";".join(autocompXML))
        else:
            event.Skip()

    def OnUpdateUI(self, _):
        # check for matching braces
        braceAtCaret = -1
        braceOpposite = -1
        charBefore = None
        styleBefore = None
        caretPos = self.GetCurrentPos()

        if caretPos > 0:
            charBefore = self.GetCharAt(caretPos - 1)
            styleBefore = self.GetStyleAt(caretPos - 1)

        # check before
        if charBefore and chr(charBefore) in "[]{}()" and styleBefore == stc.STC_P_OPERATOR:
            braceAtCaret = caretPos - 1

        # check after
        if braceAtCaret < 0:
            charAfter = self.GetCharAt(caretPos)
            styleAfter = self.GetStyleAt(caretPos)

            if charAfter and chr(charAfter) in "[]{}()" and styleAfter == stc.STC_P_OPERATOR:
                braceAtCaret = caretPos

        if braceAtCaret >= 0:
            braceOpposite = self.BraceMatch(braceAtCaret)

        if braceAtCaret != -1 and braceOpposite == -1:
            self.BraceBadLight(braceAtCaret)
        else:
            self.BraceHighlight(braceAtCaret, braceOpposite)


class FileHunter(wx.Frame):
    def __init__(self, parent, ident=wx.ID_ANY, title=wx.EmptyString, scriptpath=None, filterFiles=False,
                 manageSVN=True, enableTimers=True, grammarFile=DEFAULT_OPENFST_GRAMMAR_FILE,
                 airlineFile=DEFAULT_AIRLINE_FILE, disableASRInput=False, disableAssistance=False,
                 disableAutocomplete=False):

        # Init variables
        self.ident = ident
        self.xmlGenerationIsAborted = False
        self.formerCorrCommand = None

        self.manageSVN = manageSVN

        # Prepare concept generator
        self.conceptGenerator = ConceptGenerator(airlineFile)

        # current directory
        self.orgDir = scriptpath
        if self.orgDir is None:
            self.orgDir = path.abspath(path.dirname(sys.argv[0]))
        self.workDir = self.orgDir

        self.currentItem = None
        self.formerXMLCorr = None

        self.hasTimers = enableTimers
        self.showsASR = not disableASRInput
        self.showsAssistance = not disableAssistance
        self.hasAutocomplete = not disableAutocomplete

        self.converter = Text2XMLConverter(grammarFile, airlineFile)
        self.grammar_name = self.converter.getGrammarName()

        wx.Frame.__init__(self, parent, -1, title, size=(995, 735))
        panel = wx.Panel(self, size=wx.Size(995, 735))
        self.Bind(wx.EVT_CLOSE, self.OnClose)

        # Directory Selector
        wx.StaticText(panel, label='Directory:', pos=(12, 18), size=(80, 80))
        self.dirChooser = wx.TextCtrl(panel, pos=(90, 10), size=(320, 30))
        self.dirChooser.SetValue(self.workDir)
        self.dirChooser.SetEditable(False)

        self.Btn_Choos = wx.Button(panel, label='Change', pos=(420, 10), size=(65, 30))
        self.Btn_Choos.Bind(wx.EVT_BUTTON, self.ChooseDir)

        # Duration Info
        wx.StaticText(panel, label='Duration:', pos=(322, 250), size=(80, 80))
        self.Duration = wx.TextCtrl(panel, pos=(390, 245), size=(80, 30))

        # Buttons
        self.Btn_Play = wx.Button(panel, label='Play', pos=(320, 300), size=(50, 40))
        self.Btn_Play.Bind(wx.EVT_BUTTON, self.PlayRecords)

        self.Gauge_XML = wx.Gauge(panel, pos=(380, 340), size=(80, 10))
        self.Btn_XML = wx.Button(panel, label='Get XML', pos=(380, 300), size=(80, 40))
        self.Btn_XML.Bind(wx.EVT_BUTTON, self.toggleXMLGeneration)
        if not self.showsAssistance:
            self.Btn_XML.Disable()
        self.xmlButtonMode = BUTTON_GENERATE

        self.Btn_CMD = wx.Button(panel, label='Get Command', pos=(470, 300), size=(120, 40))
        self.Btn_CMD.Bind(wx.EVT_BUTTON, self.toggleCommandGeneration)
        if not self.showsAssistance:
            self.Btn_CMD.Disable()
        self.cmdButtonMode = BUTTON_GENERATE

        self.Btn_Save = wx.Button(panel, label='Save', pos=(600, 300), size=(50, 40))
        self.Btn_Save.Bind(wx.EVT_BUTTON, self.explicitSave)

        self.Btn_Delete = wx.Button(panel, label='Remove', pos=(900, 300), size=(80, 40))
        self.Btn_Delete.Bind(wx.EVT_BUTTON, self.moveItem2Subdir)

        if enableTimers:
            self.timer_item_is_on = False
            self.Btn_Timer_Item = wx.Button(panel, label='Start', pos=(500, 10), size=(65, 40))
            self.Btn_Timer_Item.Bind(wx.EVT_BUTTON, self.triggerTimerItem)

            self.timer_trans_is_on = False
            self.Btn_Timer_Trans = wx.Button(panel, label='Start', pos=(460, 345), size=(52, 21))
            self.Btn_Timer_Trans.Bind(wx.EVT_BUTTON, self.triggerTimerTrans)

            self.timer_XML_is_on = False
            self.Btn_Timer_XML = wx.Button(panel, label='Start', pos=(460, 470), size=(52, 21))
            self.Btn_Timer_XML.Bind(wx.EVT_BUTTON, self.triggerTimerXML)

            self.timer_cmd_is_on = False
            self.Btn_Timer_Cmd = wx.Button(panel, label='Start', pos=(460, 630), size=(52, 21))
            self.Btn_Timer_Cmd.Bind(wx.EVT_BUTTON, self.triggerTimerCmd)

        # Text fields
        wx.StaticText(panel, label='Text recognition:', pos=(12, 345), size=(150, 150))
        self.TextNoXML = wx.TextCtrl(panel, pos=(10, 365), size=(470, 100), style=wx.TE_MULTILINE)
        self.setFieldWriteable(self.TextNoXML, False)
        self.TextNoXML.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        wx.StaticText(panel, label='XML recognition output:', pos=(12, 470), size=(150, 150))
        self.TextEdit = XMLText(panel, 44, pos=(10, 490), size=(470, 135), style=wx.BORDER_SUNKEN)
        self.setFieldWriteable(self.TextEdit, False)
        self.TextEdit.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        wx.StaticText(panel, label='Recognition commands:', pos=(12, 630), size=(200, 150))
        self.RecogCommand = wx.TextCtrl(panel, pos=(10, 650), size=(470, 70), style=wx.TE_MULTILINE)
        self.setFieldWriteable(self.RecogCommand, False)
        self.RecogCommand.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        self.TextCompare = wx.StaticText(panel, label='', pos=(490, 410), size=(150, 150))
        self.XMLCompare = wx.StaticText(panel, label='', pos=(490, 535), size=(150, 150))
        self.ConceptCompare = wx.StaticText(panel, label='', pos=(490, 680), size=(150, 150))
        self.UnknownWordsWarning = wx.StaticText(panel, label='', pos=(502, 535), size=(150, 150))

        wx.StaticText(panel, label='Text transcription:', pos=(512, 345), size=(150, 150))
        self.TextCorr = wx.TextCtrl(panel, pos=(510, 365), size=(470, 100), style=wx.TE_MULTILINE)
        self.TextCorr.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        if self.converter.has_grammar:
            grammar_info = 'based on {}'.format(self.grammar_name)
        else:
            grammar_info = 'NO GRAMMAR FOUND'

        self.XMLHeadline = wx.StaticText(panel, label='XML transcription ({}):'.format(grammar_info),
                                         pos=(512, 470), size=(500, 150))
        self.XMLCorr = XMLText(panel, 45, pos=(510, 490), size=(470, 135), style=wx.BORDER_SUNKEN,
                               allowAutoComp=self.hasAutocomplete)
        self.XMLCorr.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        wx.StaticText(panel, label='Transcription commands:', pos=(512, 630), size=(200, 150))
        self.CorrCommand = wx.TextCtrl(panel, pos=(510, 650), size=(470, 70), style=wx.TE_MULTILINE)
        self.CorrCommand.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        wx.StaticText(panel, label='Comments:', pos=(512, 50), size=(150, 150))
        self.Comments = wx.TextCtrl(panel, pos=(510, 70), size=(470, 205), style=wx.TE_MULTILINE)
        self.Comments.Bind(wx.EVT_KILL_FOCUS, self.OnUpdatePlantCtrl)

        self.Center()
        self.Show(True)

        # Waveform Display
        self.wavePanel = WaveformPanel(self, 320, 100, 150, 130)

        # Wave file audio player
        self.audio_player = pyaudio.PyAudio()
        self.current_stream = None  # While an audio stream is running, it is stored here

        # Playlist
        self.filterFiles = filterFiles
        self.PlayList = MyListCtrl(panel, 1, self.conceptGenerator)
        self.PlayList.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnItemSelected, self.PlayList)
        self.updatePlaylist()

    def OnClose(self, _):
        self.saveTexts()
        self.turnOffTimerTrans()
        self.turnOffTimerXML()
        self.turnOffTimerCmd()
        self.turnOffTimerItem()
        self.audio_player.terminate()
        logging.info('Close window')
        self.Destroy()

    @staticmethod
    def setFieldWriteable(button, is_writeable=True):
        # Set Text Field Background Colour
        nowriteBG = wx.Colour(240, 240, 240)
        writeBG = wx.Colour(255, 255, 255)
        if is_writeable:
            button.SetEditable(True)
            button.SetBackgroundColour(writeBG)
        else:
            button.SetEditable(False)
            button.SetBackgroundColour(nowriteBG)

    def updatePlaylist(self):
        # Clean previous file info
        self.TextEdit.Clear()
        self.Duration.Clear()
        self.wavePanel.UpdateWaveform()
        self.loadTexts(None)
        self.resetButtons()
        self.updateMarkers()

        # Update playlist
        self.PlayList.update(self.workDir, filterFiles=self.filterFiles)

    def OkButtonPressed(self, _):
        # copy path to directory
        self.workDir = self.dirChooser.GetValue()
        self.updatePlaylist()

    def UpdateWaveform(self, wavFile):
        wavPath = path.join(self.workDir, wavFile)
        wavreader = wave.open(wavPath, 'r')
        nSamples = wavreader.getnframes()
        samplingRate = wavreader.getframerate()
        rawdata = wavreader.readframes(nSamples)
        wavreader.close()
        data = array.array('h', struct.unpack('<%ih' % nSamples, rawdata))
        self.wavePanel.UpdateWaveform(data)

        duration = float(nSamples) / samplingRate
        s = "{0:2.2f} sec".format(duration)
        self.Duration.SetValue(str(s))
        self.Duration.SetEditable(False)
        logging.info("Duration;{};{}".format(duration, self.currentItem))

    def OnItemSelected(self, event):
        self.turnOffTimerItem()

        longname = event.GetLabel()
        rootname = longname.split('. ', 1)[1]
        logging.info("Open;{}".format(rootname))
        self.loadTexts(rootname)

        self.UpdateWaveform("%s%s" % (rootname, WAV_EXT))
        self.resetButtons()
        self.updateMarkers()

        event.Skip()

    def explicitSave(self, event):
        logging.info('Save;{}'.format(self.currentItem))
        self.OnUpdatePlantCtrl(event)

    def OnUpdatePlantCtrl(self, _):
        """
        Activated when focus is shifted from wx.TextCtrl to another GUI Box
        """
        self.saveTexts()
        self.updateMarkers()

    def moveItem2Subdir(self, _):
        if self.currentItem is not None:
            itemRE = path.join(self.workDir, self.currentItem + '.*')
            subdir = path.join(self.workDir, 'removed')
            if len(self.currentItem.strip()) == 0:
                return

            # Move files
            logging.info('Delete;{}'.format(self.currentItem))
            if self.manageSVN:
                if not path.exists(subdir):
                    Popen(["svn", "mkdir", subdir]).communicate()
                for filepath in iglob(itemRE):
                    Popen(["svn", "move", '--force', filepath, subdir]).communicate()
            else:
                if not path.exists(subdir):
                    os.mkdir(subdir)
                for filepath in iglob(itemRE):
                    shutil.move(filepath, subdir)

            # Update file list
            focus = self.PlayList.GetFocusedItem()
            self.updatePlaylist()
            self.PlayList.Select(focus)

    def triggerTimerItem(self, _):
        if self.hasTimers:
            if self.timer_item_is_on:
                self.turnOffTimerTrans()
                self.turnOffTimerXML()
                self.turnOffTimerCmd()
                self.turnOffTimerItem()
            else:
                self.turnOnTimerItem()

    def triggerTimerTrans(self, _):
        if self.hasTimers:
            if self.timer_trans_is_on:
                self.turnOffTimerTrans()
            else:
                self.turnOffTimerXML()
                self.turnOffTimerCmd()
                self.turnOnTimerItem()
                self.turnOnTimerTrans()

    def triggerTimerXML(self, _):
        if self.hasTimers:
            if self.timer_XML_is_on:
                self.turnOffTimerXML()
            else:
                self.turnOffTimerTrans()
                self.turnOffTimerCmd()
                self.turnOnTimerItem()
                self.turnOnTimerXML()

    def triggerTimerCmd(self, _):
        if self.hasTimers:
            if self.timer_cmd_is_on:
                self.turnOffTimerCmd()
            else:
                self.turnOffTimerTrans()
                self.turnOffTimerXML()
                self.turnOnTimerItem()
                self.turnOnTimerCmd()

    def turnOnTimerItem(self):
        if self.hasTimers:
            if not self.timer_item_is_on and self.currentItem is not None:
                logging.info("Timer;Start;CompleteItem;{}".format(self.currentItem))
                self.timer_item_is_on = True
                self.Btn_Timer_Item.SetLabel('Pause')

    def turnOnTimerTrans(self):
        if self.hasTimers:
            if not self.timer_trans_is_on and self.currentItem is not None:
                logging.info("Timer;Start;Transcription;{}".format(self.currentItem))
                self.timer_trans_is_on = True
                self.Btn_Timer_Trans.SetLabel('Pause')

    def turnOnTimerXML(self):
        if self.hasTimers:
            if not self.timer_XML_is_on and self.currentItem is not None:
                logging.info("Timer;Start;XMLAnno;{}".format(self.currentItem))
                self.timer_XML_is_on = True
                self.Btn_Timer_XML.SetLabel('Pause')

    def turnOnTimerCmd(self):
        if self.hasTimers:
            if not self.timer_cmd_is_on and self.currentItem is not None:
                logging.info("Timer;Start;CmdAnno;{}".format(self.currentItem))
                self.timer_cmd_is_on = True
                self.Btn_Timer_Cmd.SetLabel('Pause')

    def turnOffTimerItem(self):
        if self.hasTimers:
            if self.timer_item_is_on and self.currentItem is not None:
                logging.info("Timer;Stop;CompleteItem;{}".format(self.currentItem))
                self.timer_item_is_on = False
                self.Btn_Timer_Item.SetLabel('Start')

    def turnOffTimerTrans(self):
        if self.hasTimers:
            if self.timer_trans_is_on and self.currentItem is not None:
                logging.info("Timer;Stop;Transcription;{}".format(self.currentItem))
                self.timer_trans_is_on = False
                self.Btn_Timer_Trans.SetLabel('Start')

    def turnOffTimerXML(self):
        if self.hasTimers:
            if self.timer_XML_is_on and self.currentItem is not None:
                logging.info("Timer;Stop;XMLAnno;{}".format(self.currentItem))
                self.timer_XML_is_on = False
                self.Btn_Timer_XML.SetLabel('Start')

    def turnOffTimerCmd(self):
        if self.hasTimers:
            if self.timer_cmd_is_on and self.currentItem is not None:
                logging.info("Timer;Stop;CmdAnno;{}".format(self.currentItem))
                self.timer_cmd_is_on = False
                self.Btn_Timer_Cmd.SetLabel('Start')

    def saveTexts(self):
        """
        Save all text fields to files and update tool's file listings.
        If all text was removed from a field, its file is deleted instead.
        """
        if self.currentItem is not None:
            # Get name of selected file in the List
            currentItempath = path.join(self.workDir, self.currentItem)
            # Pure-text annotation
            filepath_cor = currentItempath + TEXT_ANNO_EXT
            cor_text = self.TextCorr.GetValue().strip()
            self.editFile(filepath_cor, cor_text, self.PlayList.setTextAnno)
            # XML annotation
            filepath_xcor = currentItempath + XML_ANNO_EXT
            xcor_text = self.XMLCorr.GetValue().strip()
            self.editFile(filepath_xcor, xcor_text, self.PlayList.setXMLAnno)
            # Command annotation
            filepath_cmd = currentItempath + CMD_ANNO_EXT
            cmd_text = self.CorrCommand.GetValue().strip()
            self.editFile(filepath_cmd, cmd_text, self.PlayList.setCommandAnno)
            # Annotator comments
            filepath_nfo = currentItempath + COMMENT_EXT
            nfo_text = self.Comments.GetValue().strip()
            self.editFile(filepath_nfo, nfo_text, None)

    def editFile(self, filename, text, textSettingFunction=None):
        """
        Creates a file with the given text.
        If the text is an empty string, the file is deleted if it existed beforehand.
        if provided, the textSettingFunction is called to set the state of the file
        for the given text kind (see setXYZAnno() functions in MyListCtrl class)
        or deletes a file. if the text parameter is None or an empty string,
        the file is deleted, otherwise it is created and filled with the text.
        """
        if filename is not None:
            alreadyExists = path.exists(filename)
            if text is not None and len(text) > 0:
                if textSettingFunction is not None:
                    textSettingFunction(self.currentItem, True)
                with open(filename, 'w') as w:
                    w.write(text)
                if self.manageSVN and not alreadyExists:
                    Popen(["svn", "add", filename]).communicate()
            else:
                if textSettingFunction is not None:
                    textSettingFunction(self.currentItem, False)
                if alreadyExists:
                    if self.manageSVN:
                        Popen(["svn", "delete", '--force', filename]).communicate()
                    else:
                        os.remove(filename)

    def loadTexts(self, fileroot):
        self.currentItem = fileroot
        if self.currentItem is None:  # Load no files
            self.TextEdit.SetValue('')
            self.TextNoXML.SetValue('')
            self.RecogCommand.SetValue('')
            self.TextCorr.SetValue('')
            self.XMLCorr.SetValue('')
            self.CorrCommand.SetValue('')
            self.Comments.SetValue('')
        else:  # Load files normally
            filepath = path.join(self.workDir, self.currentItem)
            textFilename = filepath + HYP_EXT
            hypcmdFilename = filepath + CMD_HYP_EXT
            corFilename = filepath + TEXT_ANNO_EXT
            xcorFilename = filepath + XML_ANNO_EXT
            annocmdFilename = filepath + CMD_ANNO_EXT
            nfoFilename = filepath + COMMENT_EXT

            # ### Load texts ###
            # Load recognizer's hypothesis
            xmlHyp = ''
            if path.exists(textFilename):
                with open(textFilename, 'r') as fp:
                    xmlHyp = fp.read().strip()
            if self.showsASR:
                self.TextEdit.SetValue(xmlHyp)

            # Generate pure-text hypothesis
            textHyp = stripXML(xmlHyp)
            if self.showsASR:
                self.TextNoXML.SetValue(textHyp)

            # Load recognizer's commands
            cmdHyp = ''
            if path.exists(hypcmdFilename):
                with open(hypcmdFilename, 'r') as fp:
                    cmdHyp = fp.read().strip()
            if self.showsASR:
                self.RecogCommand.SetValue(cmdHyp)

            # Load pure-text annotation
            cor_text = ''
            if path.exists(corFilename):
                with open(corFilename, 'r') as cr:
                    cor_text = cr.read().strip()
            self.TextCorr.SetValue(cor_text)

            # Load xml annotation
            xcor_text = ''
            if path.exists(xcorFilename):
                with open(xcorFilename, 'r') as xcr:
                    xcor_text = xcr.read().strip()
            self.XMLCorr.SetValue(xcor_text)
            self.XMLCorr.Colourise(0, -1)

            # Load annotation commands
            annocmd_text = ''
            if path.exists(annocmdFilename):
                with open(annocmdFilename, 'r') as cmdr:
                    annocmd_text = cmdr.read().strip()
            self.CorrCommand.SetValue(annocmd_text)

            # Load annotator comments
            nfo_text = ''
            if path.exists(nfoFilename):
                with open(nfoFilename, 'r') as nfo:
                    nfo_text = nfo.read().strip()
            self.Comments.SetValue(nfo_text)

    @staticmethod
    def containsUnknownWords(string):
        matches = re.findall(r'_(\\w+?)_', string)
        for match in matches:
            if not match.startswith('_'):
                return True
        return False

    @staticmethod
    def textsEqual(text1, text2):
        """
        Compare texts, disregarding pause markers.
        Return true if texts are identical except for disregarded things.
        """
        text1 = text1.replace('__pause__', '')
        text2 = text2.replace('__pause__', '')
        text1 = text1.replace('\n', ' ')
        text2 = text2.replace('\n', ' ')
        text1 = text1.strip()
        text2 = text2.strip()

        while '  ' in text1:
            text1 = text1.replace('  ', ' ')
        while '  ' in text2:
            text2 = text2.replace('  ', ' ')
        return text1 == text2

    def updateMarkers(self):
        # self.TextCompare.
        textAnno = self.TextCorr.GetValue().strip()
        xmlAnno = self.XMLCorr.GetValue().strip()

        # Compare pure-text entries
        textHyp = self.TextNoXML.GetValue().strip()
        if len(textAnno) == 0:
            self.TextCompare.SetLabel('')
        elif self.textsEqual(textHyp, textAnno):
            self.TextCompare.SetLabel('=')
        else:
            self.TextCompare.SetLabel('#')

        # Compare xml entries
        xmlHyp = self.TextEdit.GetValue().strip()
        if len(xmlAnno) == 0:
            self.XMLCompare.SetLabel('')
        elif self.textsEqual(xmlHyp, xmlAnno):
            self.XMLCompare.SetLabel('=')
        else:
            self.XMLCompare.SetLabel('#')

        # Compare concept entries
        conceptAnno = self.CorrCommand.GetValue().strip()
        conceptHyp = self.RecogCommand.GetValue().strip()
        if len(conceptAnno) == 0:
            self.ConceptCompare.SetLabel('')
        elif self.textsEqual(conceptHyp, conceptAnno):
            self.ConceptCompare.SetLabel('=')
        else:
            self.ConceptCompare.SetLabel('#')

        # Compare pure-text and xml anntotations
        identical = self.textsEqual(textAnno, xmlAnno)
        strippedIdentical = self.textsEqual(textAnno, stripXML(xmlAnno))
        if len(xmlAnno) == 0 or (strippedIdentical and not identical):
            self.XMLHeadline.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
        else:
            self.XMLHeadline.SetForegroundColour('Red')

        # Check if XML transcription contains unknown words
        if self.containsUnknownWords(xmlAnno):
            self.UnknownWordsWarning.SetLabel('?')
        else:
            self.UnknownWordsWarning.SetLabel('')

        # Reset "Get XML" button 
        self.Btn_XML.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))

    def ChooseDir(self, _):
        dialog = wx.DirDialog(self, "Choose a directory:", defaultPath=self.workDir,
                              style=wx.DD_DEFAULT_STYLE | wx.DD_NEW_DIR_BUTTON)
        if dialog.ShowModal() == wx.ID_OK:
            self.workDir = dialog.GetPath()
            logging.info("Chose a new directory: {}".format(self.workDir))
            self.updatePlaylist()
            self.dirChooser.SetValue(self.workDir)
        dialog.Destroy()
        self.TextEdit.Clear()
        self.Duration.Clear()
        self.wavePanel.UpdateWaveform()

        self.PlayList.Select(0)
        self.PlayList.SetFocus()

    def PlayRecords(self, _):
        if self.currentItem is not None:
            filename = path.join(self.workDir, self.currentItem + WAV_EXT)
            logging.info("Play;{}".format(self.currentItem))
            t = Thread(target=self.playwave, args=(filename,))
            t.daemon = True

            t.start()

    def playwave(self, filename):
        wf = wave.open(filename, 'rb')

        def callback(in_data, frame_count, time_info, status):
            data = wf.readframes(frame_count)
            return data, pyaudio.paContinue

        stream = self.audio_player.open(format=self.audio_player.get_format_from_width(wf.getsampwidth()),
                                        channels=wf.getnchannels(),
                                        rate=wf.getframerate(),
                                        output=True,
                                        stream_callback=callback)

        # If another sound stream is already running, terminate it
        if self.current_stream:
            self.current_stream.close()
        self.current_stream = stream

        # Play the sound stream
        stream.start_stream()
        while stream.is_active():
            time.sleep(0.1)
        stream.stop_stream()
        stream.close()
        self.current_stream = None

    def toggleXMLGeneration(self, _):
        if self.xmlButtonMode == BUTTON_GENERATE:
            self.onXMLGenerationStart()
        elif self.xmlButtonMode == BUTTON_ABORT:
            self.onXMLGenerationAbort()
        elif self.xmlButtonMode == BUTTON_REVERT:
            self.onXMLGenerationRevert()
        self.saveTexts()

    def onXMLGenerationStart(self):
        self.turnOffTimerTrans()
        self.turnOffTimerCmd()
        logging.info("Start XML generation")
        self.turnOnTimerItem()
        self.turnOnTimerXML()

        cor_text = self.TextCorr.GetValue().strip()
        self.formerXMLCorr = self.XMLCorr.GetValue().strip()
        # If correction was not filled out, copy paste prediction to correction
        if len(cor_text) == 0:
            text = self.TextNoXML.GetValue().strip()
            self.TextCorr.SetValue(text)
            cor_text = self.TextCorr.GetValue()

        self.PlayList.Enable(False)
        self.Btn_XML.SetLabel('Stop XML')
        self.xmlGenerationIsAborted = False
        self.xmlButtonMode = BUTTON_ABORT
        self.updateXMLGauge(0)

        t = Thread(target=self.generateXML, args=(cor_text,))
        t.daemon = True
        t.start()

    def generateXML(self, cor_text):
        # Generate XML
        xcor_text = self.converter.convertSentence(cor_text, wxLib=wx, wxGUI=self, timeout=0)
        wx.CallAfter(self.onXMLGenerationDone, xcor_text)

    def onXMLGenerationDone(self, xcor_text):
        # Check if XML was generated
        success = xcor_text is not None

        # Update Button
        self.xmlGenerationIsAborted = False
        if xcor_text is None or xcor_text.strip() == self.formerXMLCorr:
            logging.info("XML generation failed")
            self.Btn_XML.SetLabel('Get XML')
            self.xmlButtonMode = BUTTON_GENERATE
            self.formerXMLCorr = None
        else:
            logging.info("XML generation completed")
            self.Btn_XML.SetLabel('Undo XML')
            self.xmlButtonMode = BUTTON_REVERT

        # Update GUI
        if success:
            self.XMLCorr.SetValue(xcor_text)
            self.XMLCorr.Colourise(0, -1)
        self.updateMarkers()
        if not success:
            self.Btn_XML.SetForegroundColour('Red')
        self.PlayList.Enable(True)

        # Update files
        self.saveTexts()

    def onXMLGenerationAbort(self):
        logging.info("XML generation was aborted")
        self.xmlGenerationIsAborted = True

    def onXMLGenerationRevert(self):
        logging.info("XML generation was reverted")
        self.Btn_XML.SetLabel('Get XML')
        self.xmlButtonMode = BUTTON_GENERATE
        self.updateXMLGauge(0)
        if self.formerXMLCorr is not None:
            self.XMLCorr.SetValue(self.formerXMLCorr)
            self.XMLCorr.Colourise(0, -1)
            self.formerXMLCorr = None

    def updateXMLGauge(self, progress):
        self.Gauge_XML.SetValue(progress)

    def resetXMLGenerationButton(self):
        # Reset XML generation button
        self.updateXMLGauge(0)
        self.formerXMLCorr = None
        self.xmlButtonMode = BUTTON_GENERATE
        self.Btn_XML.SetLabel('Get XML')

    def toggleCommandGeneration(self, event):
        if self.cmdButtonMode == BUTTON_GENERATE:
            self.onCommandGenerationStart(event)
        elif self.cmdButtonMode == BUTTON_REVERT:
            self.onCommandGenerationRevert()
        self.saveTexts()

    def onCommandGenerationStartOldVersion(self, event):
        """
        Generates commands, based on XML, for annotation and writes it to field.
        Possible previous content will be overwritten.
        Does nothing if XML annotation isn't filled out.
        """
        self.turnOffTimerTrans()
        self.turnOffTimerXML()
        logging.info("Start command generation")
        self.turnOnTimerItem()
        self.turnOnTimerCmd()

        xcor_text = self.XMLCorr.GetValue().strip()
        self.formerCorrCommand = self.CorrCommand.GetValue().strip()
        if len(xcor_text) > 0:
            commands = self.conceptGenerator.recognizeString(xcor_text, isStrict=False)
            if len(commands) > 0:
                cmdText = '\n'.join(commands)
            else:
                cmdText = 'NO_COMMAND'

            if cmdText.strip() == self.formerCorrCommand:
                logging.info("Command generation failed")
                self.Btn_CMD.SetLabel('Get Command')
                self.cmdButtonMode = BUTTON_GENERATE
                self.formerCorrCommand = None
            else:
                self.CorrCommand.SetValue(cmdText)
                logging.info("Command generation completed")
                self.Btn_CMD.SetLabel('Undo Command')
                self.cmdButtonMode = BUTTON_REVERT

            self.OnUpdatePlantCtrl(event)

    def onCommandGenerationStart(self, event):
        """
        Generates commands, based on XML, for annotation and writes it to field.
        Possible previous content will be overwritten.
        Does nothing if XML annotation isn't filled out.
        """
        self.turnOffTimerTrans()
        self.turnOffTimerXML()
        logging.info("Start command generation")
        self.turnOnTimerItem()
        self.turnOnTimerCmd()

        xcor_text = self.XMLCorr.GetValue().strip()
        self.formerCorrCommand = self.CorrCommand.GetValue().strip()
        if len(xcor_text) > 0:
            commands = self.conceptGenerator.recognizeString(xcor_text, isStrict=False)
            if len(commands) > 0:
                cmdText = '\n'.join(commands)
            else:
                cmdText = 'NO_COMMAND'

            if cmdText.strip() == self.formerCorrCommand:
                logging.info("Command generation failed")
                self.Btn_CMD.SetLabel('Get Command')
                self.cmdButtonMode = BUTTON_GENERATE
                self.formerCorrCommand = None
            else:
                self.CorrCommand.SetValue(cmdText)
                logging.info("Command generation completed")
                self.Btn_CMD.SetLabel('Undo Command')
                self.cmdButtonMode = BUTTON_REVERT

            self.OnUpdatePlantCtrl(event)

    def onCommandGenerationRevert(self):
        logging.info("Command generation was reverted")
        self.Btn_CMD.SetLabel('Get Command')
        self.cmdButtonMode = BUTTON_GENERATE
        if self.formerCorrCommand is not None:
            self.CorrCommand.SetValue(self.formerCorrCommand)
            self.formerCorrCommand = None

    def resetCommandGenerationButton(self):
        # Reset command generation button
        self.formerCorrCommand = None
        self.cmdButtonMode = BUTTON_GENERATE
        self.Btn_CMD.SetLabel('Get Command')

    def resetButtons(self):
        self.resetXMLGenerationButton()
        self.resetCommandGenerationButton()

        self.turnOffTimerTrans()
        self.turnOffTimerXML()
        self.turnOffTimerCmd()
        self.turnOffTimerItem()
        self.turnOnTimerItem()


def main(args):
    argparser = argparse.ArgumentParser('Load configuration for annotation tool.')
    argparser.add_argument('--logfile', '--log', '-l', help='Save logging info to the given file.')
    argparser.add_argument('--svn', '-s', action='store_true',
                           help='Also change the SVN status of files (i.e. run svn add or svn rm).')
    argparser.add_argument('--filter', '-f', action='store_true',
                           help='List only utterances for which bad utterances (e.g. annotation errors) were detected.')
    argparser.add_argument('--timer', '-t', action='store_true',
                           help='Show timer buttons')
    # Auxiliary file paths
    argparser.add_argument('--grammar', '--grammarfile', '-g', default=DEFAULT_OPENFST_GRAMMAR_FILE,
                           help='Path to file storing OpenFST-formatted grammar.')
    argparser.add_argument('--airlines', '--airlinesfile', '-i', default=DEFAULT_AIRLINE_FILE,
                           help='Path to file storing airline callsign information.')
    # Feature deactivation flags
    argparser.add_argument('--noasr', '-r', action='store_true',
                           help='Do not display ASR recognition, even if available.')
    argparser.add_argument('--noassist', '-a', action='store_true',
                           help='Do not offer assistive functions like autogeneration.')
    argparser.add_argument('--noautocomp', '-c', action='store_true',
                           help='Do not offer autocomplete and syntax highlighting.')
    pargs = argparser.parse_args()

    if pargs.logfile:
        logging.basicConfig(filename=pargs.logfile, level=logging.INFO, format='%(asctime)s;%(message)s')

    atexit.register(logExit)
    scriptpath = path.dirname(args[0])
    abspath = path.abspath(scriptpath)
    app = wx.App(0)
    FileHunter(None, -1, 'ATC-Anno', scriptpath=abspath, filterFiles=pargs.filter, manageSVN=pargs.svn,
               enableTimers=pargs.timer, grammarFile=pargs.grammar, airlineFile=pargs.airlines,
               disableASRInput=pargs.noasr, disableAssistance=pargs.noassist, disableAutocomplete=pargs.noautocomp)

    logging.info('START TOOL')
    if pargs.noautocomp:
        logging.info('Autocomplete disabled')
    else:
        logging.info('Autocomplete enabled')
    if pargs.noassist:
        logging.info('Assistance disabled')
    else:
        logging.info('Assistance enabled')
    if pargs.noasr:
        logging.info('ASR disabled')
    else:
        logging.info('ASR enabled')

    app.MainLoop()


def logExit():
    logging.info("EXIT TOOL")


if __name__ == '__main__':
    main(sys.argv)
