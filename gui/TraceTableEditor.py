# *****************************************************************
# IonControl:  Copyright 2016 Sandia Corporation
# This Software is released under the GPL license detailed
# in the file "license.txt" in the top-level IonControl directory
# *****************************************************************
from PyQt5 import QtCore, QtWidgets, QtGui
import numpy
from uiModules.RotatedHeaderView import RotatedHeaderView
from uiModules.MagnitudeSpinBoxDelegate import MagnitudeSpinBoxDelegate
from uiModules.KeyboardFilter import KeyListFilter
from modules.Utility import unique
strmap = lambda x: list(map(str, x))

class RotatedHeaderShrink(RotatedHeaderView):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        super().setSectionResizeMode(3)

class NamedTraceTableModel(QtCore.QAbstractTableModel):
    def __init__(self, uniqueSelectedNodes, model, parent=None, *args):
        QtCore.QAbstractTableModel.__init__(self, parent, *args)
        self.arraydata = []
        self.nodelookup = dict()
        self.arraylen = 0
        self.model = model
        self.dataLookup = {
            (QtCore.Qt.DisplayRole): lambda index: str(self.nodelookup[index.column()]['data'][index.row()]),
            (QtCore.Qt.EditRole): lambda index: self.nodelookup[index.column()]['data'][index.row()]
            }
        for node in uniqueSelectedNodes:
            dataNodes = model.getDataNodes(node)
            for dataNode in dataNodes:
                self.dataChanged.connect(dataNode.content.replot, QtCore.Qt.UniqueConnection)
                self.constructArray(dataNode.content)

    def rowCount(self, parent):
        return len(self.nodelookup[0]['data'])

    def columnCount(self, parent):
        return len(self.nodelookup)

    def data(self, index, role):
        if index.isValid():
            return self.dataLookup.get(role, lambda index: None)(index)

    def setData(self, index, value, role):
        if role == QtCore.Qt.EditRole:
            self.nodelookup[index.column()]['data'][index.row()] = float(value)
            self.dataChanged.emit(index, index)
            return True
        return False

    def setValue(self, index, value):
        self.setData(index, value, QtCore.Qt.EditRole)

    def flags(self, index):
        return QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable

    def constructArray(self, datain):
        if self.arraylen == 0 or not numpy.array_equal(self.nodelookup[self.arraylen-1]['parent'].trace.x, datain.trace.x):
            self.nodelookup[self.arraylen] = {'name': datain.name, 'xy': 'x', 'data': datain.traceCollection[datain._xColumn], 'column': datain._xColumn, 'parent': datain}
            self.arraylen += 1
        self.nodelookup[self.arraylen] = {'name': datain.name, 'xy': 'y', 'data': datain.traceCollection[datain._yColumn], 'column': datain._yColumn, 'parent': datain}
        self.arraylen += 1

    def headerData(self, column, orientation, role=QtCore.Qt.DisplayRole):
        if role != QtCore.Qt.DisplayRole:
            return QtCore.QAbstractTableModel.headerData(self, column, orientation, role)
        if orientation == QtCore.Qt.Horizontal:
            return self.nodelookup[column]['name']+'.'+self.nodelookup[column]['xy']
        return QtCore.QAbstractTableModel.headerData(self, column, orientation, role)

    def insertRow(self, position, index=QtCore.QModelIndex()):
        numRows = len(self.nodelookup[0]['data'])
        for k, v in self.nodelookup[0]['parent'].traceCollection.items():
            if type(v) is numpy.ndarray and len(v) > 0:
                self.nodelookup[0]['parent'].traceCollection[k] = numpy.insert(v, position+1, 0.0)
        for k, v in self.nodelookup.items():
            self.nodelookup[k]['data'] = self.nodelookup[k]['parent'].traceCollection[self.nodelookup[k]['column']]
        self.dataChanged.emit(QtCore.QModelIndex(), QtCore.QModelIndex())
        self.layoutChanged.emit()
        return range(position, numRows)

    def copy_rows(self, rows, position):
        for k, v in self.nodelookup[0]['parent'].traceCollection.items():
            if type(v) is numpy.ndarray and len(v) > 0:
                self.nodelookup[0]['parent'].traceCollection[k] = numpy.insert(v, position+1, v[rows])
        for k, v in self.nodelookup.items():
            self.nodelookup[k]['data'] = self.nodelookup[k]['parent'].traceCollection[self.nodelookup[k]['column']]
        self.dataChanged.emit(QtCore.QModelIndex(), QtCore.QModelIndex())
        self.layoutChanged.emit()
        return True

    def removeRows(self, position, rows=1, index=QtCore.QModelIndex()):
        for k, v in self.nodelookup[0]['parent'].traceCollection.items():
            if type(v) is numpy.ndarray and len(v) > 0:
                self.nodelookup[0]['parent'].traceCollection[k] = numpy.delete(self.nodelookup[0]['parent'].traceCollection[k], range(position, position+rows))
        for k, v in self.nodelookup.items():
            self.nodelookup[k]['data'] = self.nodelookup[k]['parent'].traceCollection[self.nodelookup[k]['column']]
        self.dataChanged.emit(QtCore.QModelIndex(), QtCore.QModelIndex())
        self.layoutChanged.emit()
        return range(position, rows)

    def clearContents(self, indices):
        for i in indices:
            self.nodelookup[i.column()]['data'][i.row()] = float(0.0)
        self.dataChanged.emit(QtCore.QModelIndex(), QtCore.QModelIndex())
        return True

    def moveRow(self, rows, delta):
        if len(rows)>0 and (rows[0]>0 or delta>0) and (len(self.nodelookup[0]['data']) > max(rows)+1 or delta < 0):
            for k, v in self.nodelookup[0]['parent'].traceCollection.items():
                if type(v) is numpy.ndarray and len(v) > 0:
                    for row in rows:
                        self.nodelookup[0]['parent'].traceCollection[k][row], self.nodelookup[0]['parent'].traceCollection[k][row+delta] = self.nodelookup[0]['parent'].traceCollection[k][row+delta], self.nodelookup[0]['parent'].traceCollection[k][row]
            self.dataChanged.emit(QtCore.QModelIndex(), QtCore.QModelIndex())
            return True
        return False

class TraceTableEditor(QtWidgets.QWidget):
    finishedEditing = QtCore.pyqtSignal()
    def __init__(self, *args):
        super().__init__(*args)

    def setupUi(self, plottedTrace, model):
        self.tablemodel = NamedTraceTableModel(plottedTrace, model, self)
        self.tableview = QtWidgets.QTableView()
        self.tableview.setModel(self.tablemodel)
        self.delegate = MagnitudeSpinBoxDelegate()
        self.tableview.setItemDelegate(self.delegate)
        self.tableview.setHorizontalHeader(RotatedHeaderShrink(QtCore.Qt.Horizontal, self.tableview))
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.tableview)
        self.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

        self.addRow = QtWidgets.QAction("Add Row", self)
        self.addRow.triggered.connect(self.onAddRow)
        self.addAction(self.addRow)

        self.removeRow = QtWidgets.QAction("Remove Rows", self)
        self.removeRow.triggered.connect(self.onRemoveRow)
        self.addAction(self.removeRow)

        self.clearContents = QtWidgets.QAction("Clear Contents", self)
        self.clearContents.triggered.connect(self.onClearContents)
        self.addAction(self.clearContents)

        self.filter = KeyListFilter( [QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown] )
        self.filter.keyPressed.connect( self.onReorder )
        self.tableview.installEventFilter(self.filter)

        QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Copy), self, self.copy_to_clipboard)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Paste), self, self.paste_from_clipboard)

        self.resize(950, 650)
        self.move(300, 300)
        self.setWindowTitle('Trace Table Editor')
        self.show()

    def closeEvent(self, a):
        self.finishedEditing.emit()

    def onAddRow(self):
        zeroColSelInd = self.tableview.selectedIndexes()
        self.tablemodel.insertRow(zeroColSelInd[0].row())

    def onRemoveRow(self):
        zeroColSelInd = self.tableview.selectedIndexes()
        initRow = zeroColSelInd[0].row()
        finRow = zeroColSelInd[-1].row()-initRow+1
        self.tablemodel.removeRows(initRow, finRow)

    def onClearContents(self):
        zeroColSelInd = self.tableview.selectedIndexes()
        self.tablemodel.clearContents(zeroColSelInd)

    def onReorder(self, key):
        if key in [QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown]:
            indexes = self.tableview.selectedIndexes()
            up = key == QtCore.Qt.Key_PageUp
            delta = -1 if up else 1
            rows = sorted(unique([i.row() for i in indexes]), reverse=not up)
            if self.tablemodel.moveRow( rows, delta):
                selectionModel = self.tableview.selectionModel()
                selectionModel.clearSelection()
                for index in indexes:
                    selectionModel.select(self.tablemodel.createIndex(index.row()+delta, index.column()), QtCore.QItemSelectionModel.Select)

    def copy_to_clipboard(self):
        """ Copy the list of selected rows to the clipboard as a string. """
        clip = QtWidgets.QApplication.clipboard()
        rows = sorted(unique([ i.row() for i in self.tableview.selectedIndexes()]))
        clip.setText(str(rows))

    def paste_from_clipboard(self):
        """ Append the string of rows from the clipboard to the end of the TODO list. """
        clip = QtWidgets.QApplication.clipboard()
        row_string = str(clip.text())
        try:
            row_list = list(map(int, row_string.strip('[]').split(',')))
        except ValueError:
            raise ValueError("Invalid data on clipboard. Cannot paste into TODO list")
        zeroColSelInd = self.tableview.selectedIndexes()
        initRow = zeroColSelInd[-1].row()
        self.tablemodel.copy_rows(row_list, initRow)
