import sys
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import QVariant, Qt, QModelIndex
import numpy as np
import pandas as pd


# %%

class PandasModel(QtCore.QAbstractTableModel):
    def __init__(self, data=pd.DataFrame([], columns=['Use', 'Pxl', 'wl (nm)']), parent=None):
        super().__init__(parent)
        self._data = data

    def setHeaderData(self, section, orientation, value):
        if section == 2 and orientation == Qt.Horizontal:
            names = self._data.columns
            self._data = self._data.rename(columns={names[section]: value})

            self.headerDataChanged.emit(orientation, 0, section)



    def rowCount(self, parent=None):
        return len(self._data.values)

    def columnCount(self, parent=None):
        return self._data.columns.size

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid():
            if role == Qt.DisplayRole or role == Qt.EditRole:
                if index.column() != 0:
                    dat = self._data.iat[index.row(), index.column()]
                    return float(dat)
            elif role == Qt.CheckStateRole:
                if index.column() == 0:
                    if self._data.iat[index.row(), index.column()]:
                        return Qt.Checked
                    else:
                        return Qt.Unchecked

        return QVariant()

    def setData(self, index, value, role=Qt.EditRole):
        if index.isValid():
            if role == Qt.EditRole:

                self._data.iat[index.row(), index.column()] = value
                self.dataChanged.emit(index, index, [role])
                self._data
                return True
            elif role == Qt.CheckStateRole:
                self._data.iat[index.row(), index.column()] = bool(value)
                self.dataChanged.emit(index, index, [role])
                self._data
                return True

        return False

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                return self._data.columns[section]
            else:
                return self._data.index[section]
        else:
            return QtCore.QVariant()

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsEnabled
        if index.column() == 0:
            return Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        else:
            return Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable


if __name__ == '__main__':
    data = [

        [True, 100, 5,],

        [False, 50, 2.5,],

        [True, 110, 6,],
    ]
    data_df = pd.DataFrame(data, columns=['Use', 'Pxl', 'wl (nm)'])

    app = QtWidgets.QApplication(sys.argv)
    view = QtWidgets.QTableView()
    view.horizontalHeader().setSectionResizeMode(view.horizontalHeader().Stretch)
    model = PandasModel(data_df)
    view.setModel(model)

    view.show()
    sys.exit(app.exec_())