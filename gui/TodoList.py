# *****************************************************************
# IonControl:  Copyright 2016 Sandia Corporation
# This Software is released under the GPL license detailed
# in the file "license.txt" in the top-level IonControl directory
# *****************************************************************

from PyQt5 import uic, QtCore, QtGui, QtWidgets

from modules.AttributeComparisonEquality import AttributeComparisonEquality
from modules.statemachine import Statemachine
from .TodoListTableModel import TodoListTableModel
from uiModules.KeyboardFilter import KeyListFilter
from modules.Utility import unique
from functools import partial
from modules.ScanDefinition import ScanSegmentDefinition
import logging
from modules.HashableList import HashableList
from copy import deepcopy
from modules.PyqtUtility import updateComboBoxItems
from modules.SequenceDict import SequenceDict
from gui.TodoListSettingsTableModel import TodoListSettingsTableModel 
from uiModules.ComboBoxDelegate import ComboBoxDelegate
from uiModules.MagnitudeSpinBoxDelegate import MagnitudeSpinBoxDelegate
from modules.GuiAppearance import saveGuiState, restoreGuiState   #@UnresolvedImport
from modules.firstNotNone import firstNotNone

Form, Base = uic.loadUiType('ui/TodoList.ui')


class TodoListEntry(object):
    def __init__(self, scan=None, measurement=None, evaluation=None, analysis=None):
        self.parent = None
        self.children = list()
        self.scan = scan
        self.evaluation = evaluation
        self.measurement = measurement
        self.analysis = analysis
        self.scanParameter = None
        self.enabled = True
        self.stopFlag = False
        self.scanSegment = ScanSegmentDefinition()
        self.settings = SequenceDict()
        self.revertSettings = False
        
    def __setstate__(self, s):
        self.__dict__ = s
        self.__dict__.setdefault('scanParameter', None )
        self.__dict__.setdefault('measurement', None )
        self.__dict__.setdefault('scan', None )
        self.__dict__.setdefault('evaluation', None )
        self.__dict__.setdefault('enabled', True )
        self.__dict__.setdefault('stopFlag', False)
        self.__dict__.setdefault('settings', SequenceDict())
        self.__dict__.setdefault('revertSettings', False)
        self.__dict__.setdefault('analysis', None)
        self.scan = str(self.scan) if self.scan is not None else None

    stateFields = ['scan', 'measurement', 'scanParameter', 'evaluation', 'analysis', 'settings', 'enabled', 'stopFlag' ]

    def __eq__(self, other):
        return isinstance(other, self.__class__) and tuple(getattr(self, field) for field in self.stateFields)==tuple(getattr(other, field) for field in self.stateFields)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        if not isinstance(self.todoList, HashableList):
            logging.getLogger(__name__).info("Replacing list with hashable list")
            self.todoList = HashableList(self.todoList)
        return hash(tuple(getattr(self, field) for field in self.stateFields))
    

class Settings:
    def __init__(self):
        self.todoList = list()
        self.currentIndex = 0
        self.repeat = False
        
    def __setstate__(self, state):
        self.__dict__ = state
        self.__dict__.setdefault( 'currentIndex', 0)
        self.__dict__.setdefault( 'repeat', False)

    stateFields = ['currentIndex', 'repeat', 'todoList'] 
        
    def __eq__(self, other):
        return isinstance(other, self.__class__) and tuple(getattr(self, field) for field in self.stateFields)==tuple(getattr(other, field) for field in self.stateFields)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        if not isinstance(self.todoList, HashableList):
            logging.getLogger(__name__).info("Replacing list with hashable list")
            self.todoList = HashableList(self.todoList)
        return hash(tuple(getattr(self, field) for field in self.stateFields))
    

class MasterSettings(AttributeComparisonEquality):
    def __init__(self):
        self.currentSettingName = None
        self.autoSave = False

    def __setstate__(self, d):
        self.__dict__ = d
        self.__dict__.setdefault( 'autoSave', False )

class TodoList(Form, Base):
    def __init__(self,scanModules,config,currentScan,setCurrentScan,globalVariablesUi,parent=None):
        Base.__init__(self, parent)    
        Form.__init__(self)
        self.config = config
        self.settings = config.get('TodolistSettings', Settings())
        self.settingsCache = config.get( 'TodolistSettings.Cache', dict())
        self.masterSettings = config.get( 'Todolist.MasterSettings', MasterSettings())
        self.scanModules = scanModules
        self.scanModuleMeasurements = dict()
        self.scanModuleEvaluations = dict()
        self.scanModuleAnalysis = dict()
        self.currentMeasurementsDisplayedForScan = None
        self.currentScan = currentScan
        self.setCurrentScan = setCurrentScan
        self.globalVariablesUi = globalVariablesUi
        self.revertGlobalsList = list()
        self.idleConfiguration = None

    def setupStatemachine(self):
        self.statemachine = Statemachine()        
        self.statemachine.addState( 'Idle', self.enterIdle, self.exitIdle  )
        self.statemachine.addState( 'MeasurementRunning')
        self.statemachine.addState( 'Waiting for Completion', lambda:self.statusLabel.setText('Waiting for completion of measurement') )
        self.statemachine.addStateGroup('InMeasurement', ['MeasurementRunning', 'Waiting for Completion'], self.enterMeasurementRunning, self.exitMeasurementRunning )
        self.statemachine.addState( 'Check' )
        self.statemachine.addState( 'Paused', self.enterPaused )
        self.statemachine.initialize( 'Idle' )
        self.statemachine.addTransition('startCommand', 'Idle', 'MeasurementRunning', self.checkReadyToRun )
        self.statemachine.addTransitionList('stopCommand', ['Idle', 'Paused'], 'Idle')
        self.statemachine.addTransition( 'stopCommand', 'MeasurementRunning', 'Waiting for Completion')
        self.statemachine.addTransition('measurementFinished', 'MeasurementRunning', 'Idle', self.checkStopFlag)
        self.statemachine.addTransition('measurementFinished', 'MeasurementRunning', 'Check', lambda state: not self.checkStopFlag(state) and self.checkReadyToRun(state))
        self.statemachine.addTransition('measurementFinished', 'Waiting for Completion', 'Idle')
        self.statemachine.addTransition('docheck', 'Check', 'MeasurementRunning', lambda state: (self.settings.currentIndex>0 or self.settings.repeat) and self.isSomethingTodo())
        self.statemachine.addTransition('docheck', 'Check', 'Idle', lambda state: (self.settings.currentIndex == 0 and not self.settings.repeat) or not self.isSomethingTodo())
                
    def setupUi(self):
        super(TodoList, self).setupUi(self)
        self.setupStatemachine()
        self.populateMeasurements()
        self.scanSelectionBox.addItems( list(self.scanModuleMeasurements.keys()) )
        self.scanSelectionBox.currentIndexChanged[str].connect( self.updateMeasurementSelectionBox )
        self.updateMeasurementSelectionBox( self.scanSelectionBox.currentText() )
        self.tableModel = TodoListTableModel( self.settings.todoList )
        self.tableModel.valueChanged.connect( self.checkSettingsSavable )
        self.tableView.setModel( self.tableModel )
        self.comboBoxDelegate = ComboBoxDelegate()
        for column in range(1, 5):
            self.tableView.setItemDelegateForColumn(column, self.comboBoxDelegate)
        self.tableModel.measurementSelection = self.scanModuleMeasurements
        self.tableModel.evaluationSelection = self.scanModuleEvaluations     
        self.tableModel.analysisSelection = self.scanModuleAnalysis     
        self.addMeasurementButton.clicked.connect( self.onAddMeasurement )
        self.removeMeasurementButton.clicked.connect( self.onDropMeasurement )
        self.runButton.clicked.connect( partial( self.statemachine.processEvent, 'startCommand' ) )
        self.stopButton.clicked.connect( partial( self.statemachine.processEvent, 'stopCommand' ) )
        self.repeatButton.setChecked( self.settings.repeat )
        self.repeatButton.clicked.connect( self.onRepeatChanged )
        self.filter = KeyListFilter( [QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown] )
        self.filter.keyPressed.connect( self.onReorder )
        self.tableView.installEventFilter(self.filter)
        self.tableModel.setActiveRow(self.settings.currentIndex, False)
        self.tableView.doubleClicked.connect( self.setCurrentIndex )
        # naming and saving of todo lists
        self.toolButtonDelete.clicked.connect( self.onDeleteSaveTodoList )
        self.toolButtonSave.clicked.connect( self.onSaveTodoList )
        self.toolButtonReload.clicked.connect( self.onLoadTodoList )
        self.comboBoxListCache.addItems( sorted(self.settingsCache.keys()) )
        if self.masterSettings.currentSettingName is not None and self.masterSettings.currentSettingName in self.settingsCache:
            self.comboBoxListCache.setCurrentIndex( self.comboBoxListCache.findText(self.masterSettings.currentSettingName))
        self.comboBoxListCache.currentIndexChanged[str].connect( self.onLoadTodoList )
        self.checkSettingsSavable()
        self.setContextMenuPolicy( QtCore.Qt.ActionsContextMenu )
        self.autoSaveAction = QtWidgets.QAction( "auto save", self )
        self.autoSaveAction.setCheckable(True)
        self.autoSaveAction.setChecked( self.masterSettings.autoSave )
        self.autoSaveAction.triggered.connect( self.onAutoSaveChanged )
        self.addAction( self.autoSaveAction )
        # Settings
        self.settingTableModel = TodoListSettingsTableModel( SequenceDict(), self.globalVariablesUi.globalDict )
        self.settingTableView.setModel( self.settingTableModel )
        self.settingTableModel.edited.connect( self.checkSettingsSavable )
        self.comboBoxDelegate = ComboBoxDelegate()
        self.magnitudeSpinBoxDelegate = MagnitudeSpinBoxDelegate()
        self.settingTableView.setItemDelegateForColumn( 0, self.comboBoxDelegate )
        self.settingTableView.setItemDelegateForColumn( 1, self.magnitudeSpinBoxDelegate )
        self.addSettingButton.clicked.connect( self.onAddSetting )
        self.removeSettingButton.clicked.connect( self.onRemoveSetting )
        self.tableView.selectionModel().currentChanged.connect( self.onActiveItemChanged )
        # Context Menu for Table
        self.tableView.setContextMenuPolicy( QtCore.Qt.ActionsContextMenu )
        self.loadLineAction = QtWidgets.QAction( "load line settings", self)
        self.loadLineAction.triggered.connect( self.onLoadLine  )
        self.tableView.addAction( self.loadLineAction )
        # set stop flag
        self.enableStopFlagAction = QtGui.QAction( "toggle stop flag" , self)
        self.enableStopFlagAction.triggered.connect( self.onEnableStopFlag  )
        self.tableView.addAction( self.enableStopFlagAction )

        # 
        restoreGuiState( self, self.config.get('Todolist.guiState'))
        
        # Copy rows
        QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Copy), self, self.copy_to_clipboard)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Paste), self, self.paste_from_clipboard)
        #QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Delete), self, self.delete_row)

    def copy_to_clipboard(self):
        """ Copy the list of selected rows to the clipboard as a string. """
        clip = QtWidgets.QApplication.clipboard()
        rows = [ i.row() for i in self.tableView.selectedIndexes()]
        clip.setText(str(rows))
        
    def paste_from_clipboard(self):
        """ Append the string of rows from the clipboard to the end of the TODO list. """
        clip = QtWidgets.QApplication.clipboard()
        
        row_string = str(clip.text())
        try:
            row_list = list(map(int, row_string.strip('[]').split(',')))
        except ValueError:
            raise ValueError("Invalid data on clipboard. Cannot paste into TODO list")
    
        # Stuff
        self.tableModel.copy_rows(row_list)

    def onActiveItemChanged(self, modelIndex, modelIndex2 ):
        self.settingTableModel.setSettings( self.settings.todoList[modelIndex.row()].settings )
        self.currentlySelectedLabel.setText( "{0} - {1}".format( self.settings.todoList[modelIndex.row()].measurement, self.settings.todoList[modelIndex.row()].evaluation) )
        
    def onAddSetting(self):
        self.settingTableModel.addSetting()
        
    def onRemoveSetting(self):
        for index in sorted(unique([ i.row() for i in self.settingTableView.selectedIndexes() ]), reverse=True):
            self.settingTableModel.dropSetting(index)
        
    def onAutoSaveChanged(self, state):
        self.masterSettings.autoSave = state
        if state:
            self.checkSettingsSavable()
        
    def checkSettingsSavable(self, savable=None):
        if savable is None:
            text = str(self.comboBoxListCache.currentText())
            savable = False
            if text is not None and text !=  "":
                savable = text!=self.masterSettings.currentSettingName or text not in self.settingsCache or self.settings!=self.settingsCache[text]
        if savable and self.masterSettings.autoSave:
            self.onSaveTodoList()
            savable = False
        self.toolButtonSave.setEnabled( savable )
        return savable
        
    def onSaveTodoList(self):
        text = str(self.comboBoxListCache.currentText())
        if text is not None and text !=  "":
            new = text not in self.settingsCache
            if new or self.settings!=self.settingsCache[text]:
                self.settingsCache[text] = deepcopy( self.settings )
                self.masterSettings.currentSettingName = text
                if new:
                    updateComboBoxItems(self.comboBoxListCache, sorted(self.settingsCache.keys()))
        self.checkSettingsSavable(savable=False)
            
    def onDeleteSaveTodoList(self):
        text = str(self.comboBoxListCache.currentText())
        if text in self.settingsCache:
            self.settingsCache.pop(text)
            updateComboBoxItems(self.comboBoxListCache, sorted(self.settingsCache.keys()))
            
    def onLoadTodoList(self, text=None):
        text = str(text) if text is not None else str(self.comboBoxListCache.currentText())
        if text in self.settingsCache:
            self.masterSettings.currentSettingName = text
            self.setSettings( deepcopy( self.settingsCache[text] ) )
        self.checkSettingsSavable()
        
    def setSettings(self, newSettings):
        self.settings = newSettings
        self.tableModel.setTodolist(self.settings.todoList)
        self.tableModel.setActiveRow( self.settings.currentIndex, self.statemachine.currentState=='MeasurementRunning' )
        self.repeatButton.setChecked( self.settings.repeat )
        
    def setCurrentIndex(self, index):
        self.settings.currentIndex = index.row()
        self.tableModel.setActiveRow(self.settings.currentIndex, self.statemachine.currentState=='MeasurementRunning')      
        self.checkSettingsSavable()  
        
    def updateMeasurementSelectionBox(self, newscan ):
        newscan = str(newscan)
        if self.currentMeasurementsDisplayedForScan != newscan:
            self.currentMeasurementsDisplayedForScan = newscan
            updateComboBoxItems(self.measurementSelectionBox, self.scanModuleMeasurements[newscan] )
            updateComboBoxItems(self.evaluationSelectionBox, self.scanModuleEvaluations[newscan] )
            updateComboBoxItems(self.analysisSelectionBox, self.scanModuleAnalysis[newscan] )
        
    def populateMeasurements(self):
        self.scanModuleMeasurements = dict()
        for name, widget in self.scanModules.items():
            if hasattr(widget, 'scanControlWidget' ):
                self.populateMeasurementsItem( name, widget.scanControlWidget.settingsDict )
            else:
                self.populateMeasurementsItem( name, {} )                
            if hasattr(widget, 'evaluationControlWidget' ):
                self.populateEvaluationItem( name, widget.evaluationControlWidget.settingsDict )
            else:
                self.populateEvaluationItem( name, {} )
            if hasattr(widget, 'analysisControlWidget' ):
                self.populateAnalysisItem( name, widget.analysisControlWidget.analysisDefinitionDict )
            else:
                self.populateAnalysisItem( name, {} )
        if hasattr(self, 'tableModel'):
            self.tableModel.measurementSelection = self.scanModuleMeasurements
            self.tableModel.evaluationSelection = self.scanModuleEvaluations     
            self.tableModel.analysisSelection = self.scanModuleAnalysis     
                
    def populateMeasurementsItem(self, name, settingsDict ):
        self.scanModuleMeasurements[name] = sorted(settingsDict.keys())
        if name == self.currentMeasurementsDisplayedForScan:
            updateComboBoxItems( self.measurementSelectionBox, self.scanModuleMeasurements[name] )            

    def populateEvaluationItem(self, name, settingsDict ):
        self.scanModuleEvaluations[name] = sorted(settingsDict.keys())
        if name == self.currentMeasurementsDisplayedForScan:
            updateComboBoxItems( self.evaluationSelectionBox, self.scanModuleEvaluations[name] )            

    def populateAnalysisItem(self, name, settingsDict ):
        self.scanModuleAnalysis[name] = sorted(settingsDict.keys())
        if name == self.currentMeasurementsDisplayedForScan:
            updateComboBoxItems( self.analysisSelectionBox, self.scanModuleAnalysis[name] )            

    def onReorder(self, key):
        if key in [QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown]:
            indexes = self.tableView.selectedIndexes()
            up = key==QtCore.Qt.Key_PageUp
            delta = -1 if up else 1
            rows = sorted(unique([ i.row() for i in indexes ]), reverse=not up)
            if self.tableModel.moveRow( rows, up=up ):
                selectionModel = self.tableView.selectionModel()
                selectionModel.clearSelection()
                for index in indexes:
                    selectionModel.select( self.tableModel.createIndex(index.row()+delta, index.column()), QtCore.QItemSelectionModel.Select )
#            self.selectionChanged.emit( self.enabledParametersObjects )
        self.checkSettingsSavable()

    def onAddMeasurement(self):
        if self.currentMeasurementsDisplayedForScan and self.measurementSelectionBox.currentText():
            self.tableModel.addMeasurement( TodoListEntry(self.currentMeasurementsDisplayedForScan, str(self.measurementSelectionBox.currentText()), 
                                                          str(self.evaluationSelectionBox.currentText()), str(self.analysisSelectionBox.currentText())))
        self.checkSettingsSavable()
    
    def onDropMeasurement(self):
        for index in sorted(unique([ i.row() for i in self.tableView.selectedIndexes() ]), reverse=True):
            self.tableModel.dropMeasurement(index)
        numEntries = self.tableModel.rowCount()
        if self.settings.currentIndex >= numEntries:
            self.settings.currentIndex = 0
        self.checkSettingsSavable()

    def checkReadyToRun(self, state, _=True ):
        _, current = self.currentScan()
        return current.state()==0 and self.isSomethingTodo()

    def checkStopFlag(self, state):
        return self.settings.todoList[self.settings.currentIndex].stopFlag

    def onStateChanged(self, newstate ):
        if newstate=='idle':
            self.statemachine.processEvent('measurementFinished')
            self.statemachine.processEvent('docheck')
    
    def onRepeatChanged(self, enabled):
        self.settings.repeat = enabled
        self.checkSettingsSavable()

    def enterIdle(self):
        self.statusLabel.setText('Idle')
        if self.idleConfiguration is not None:
            (previousName, previousScan, previousEvaluation, previousAnalysis) = self.idleConfiguration
            currentname, currentwidget = self.currentScan()
            if previousName!=currentname:
                self.setCurrentScan(previousName)
            currentwidget.scanControlWidget.loadSetting( previousScan )   
            currentwidget.evaluationControlWidget.loadSetting( previousEvaluation )  
            currentwidget.analysisControlWidget.onLoadAnalysisConfiguration( previousAnalysis )
        
    def exitIdle(self):
        currentname, currentwidget = self.currentScan()
        currentScan = currentwidget.scanControlWidget.settingsName  
        currentEvaluation = currentwidget.evaluationControlWidget.settingsName
        currentAnalysis = currentwidget.analysisControlWidget.currentAnalysisName
        self.idleConfiguration = (currentname, currentScan, currentEvaluation, currentAnalysis)
        
    def onLoadLine(self):
        allrows = sorted(unique([ i.row() for i in self.tableView.selectedIndexes() ]))
        if len(allrows)==1: 
            self.loadLine(self.settings.todoList[ allrows[0] ])
            
    def loadLine(self, entry ):
        currentname, currentwidget = self.currentScan()
        # switch to the scan for the first line
        if entry.scan!=currentname:
            self.setCurrentScan(entry.scan)
        # load the correct measurement
        currentwidget.scanControlWidget.loadSetting( entry.measurement )   
        currentwidget.evaluationControlWidget.loadSetting( entry.evaluation )  
        currentwidget.analysisControlWidget.onLoadAnalysisConfiguration( firstNotNone( entry.analysis, "")  )

    def onEnableStopFlag(self):
        allrows = sorted(unique([ i.row() for i in self.tableView.selectedIndexes() ]))
        if len(allrows)==1:
            self.enableStopFlag(self.settings.todoList[ allrows[0] ])

    def enableStopFlag(self, entry ):
        entry.stopFlag = not entry.stopFlag
        
    def isSomethingTodo(self):
        for index in list(range( self.settings.currentIndex, len(self.settings.todoList))) + (list(range(0, self.settings.currentIndex)) if self.settings.repeat else []):
            if self.settings.todoList[ index ].enabled:
                self.settings.currentIndex = index
                return True
        return False
                
    def enterMeasurementRunning(self):
        entry = self.settings.todoList[ self.settings.currentIndex ]            
        self.statusLabel.setText('Measurement Running')
        _, currentwidget = self.currentScan()
        self.loadLine( entry )
        # set the global variables
        #self.revertGlobalsList = [('Global', key, self.globalVariablesUi.globalDict[key]) for key in entry.settings.iterkeys()]
        #self.globalVariablesUi.update( ( ('Global', k, v) for k,v in entry.settings.items() ))
        # start
        currentwidget.onStart([(k, v) for k, v in entry.settings.items()])
        self.tableModel.setActiveRow(self.settings.currentIndex, True)
        
    def exitMeasurementRunning(self):
        self.settings.currentIndex = (self.settings.currentIndex+1) % len(self.settings.todoList)
        self.tableModel.setActiveRow(self.settings.currentIndex, False)
        #self.globalVariablesUi.update(self.revertGlobalsList)
        
    def enterPaused(self):
        self.statusLabel.setText('Paused')
        
    def saveConfig(self):
        self.config['TodolistSettings'] = self.settings
        self.config['TodolistSettings.Cache'] = self.settingsCache
        self.config['Todolist.MasterSettings'] = self.masterSettings
        self.config['Todolist.guiState'] = saveGuiState( self )
       
        
        