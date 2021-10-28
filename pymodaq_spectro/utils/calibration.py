import sys
import pandas as pd
import numpy as np

from qtpy import QtGui, QtWidgets
from qtpy.QtCore import QObject, Slot, Signal, QLocale, QDateTime, QRectF, QDate, QThread, Qt
from pyqtgraph.dockarea import Dock
from pymodaq.daq_utils.gui_utils import DockArea
from pymodaq.daq_utils.plotting.viewer1D.viewer1D_main import Viewer1D
from pyqtgraph.parametertree import Parameter, ParameterTree
from pymodaq_spectro.utils.utils_classes import PandasModel

from pymodaq.daq_utils.h5modules import browse_data
import pyqtgraph.parametertree.parameterTypes as pTypes
import pymodaq.daq_utils.custom_parameter_tree as custom_tree
from pyqtgraph import TextItem, ArrowItem
from pymodaq.daq_utils.daq_utils import Enm2cmrel, Ecmrel2Enm, nm2eV, eV2nm, eV2radfs, l2w, set_logger, get_module_name
from pathlib import Path
from scipy.signal import find_peaks

logger = set_logger(get_module_name(__file__))
peak_options = ['Height', 'Threshold', 'Distance', 'Prominence', 'Width',]

class PeakGroup(pTypes.GroupParameter):
    def __init__(self, **opts):
        opts['type'] = 'group'
        opts['addText'] = "Add"
        opts['addList'] = peak_options
        self.channels = opts['channels']
        pTypes.GroupParameter.__init__(self, **opts)
        self.preset = dict(height=0, threshold=0, distance=1, prominence=0.5, width=1)

    def addNew(self, typ=''):
        indexes = [int(par.name()[-2:]) for par in self.children()]
        if indexes == []:
            newindex = 0
        else:
            newindex = max(indexes) + 1
        child = {'title': 'Peak option', 'name': 'peak_option_{:02d}'.format(newindex), 'type': 'group', 'removable': True, 'renamable': False}

        children = [{'title': 'Channel', 'name': 'channel', 'type': 'list', 'values': self.channels,},
                    {'name': typ.lower(), 'type': 'float', 'value': self.preset[typ.lower()],},
                    {'title': 'Use?', 'name': 'use_opts', 'type': 'bool', 'value': False}
                    ]
        child['children'] = children
        self.addChild(child)



class Calibration(QtWidgets.QWidget):
    log_signal = Signal(str)
    coeffs_calib = Signal(list)

    params = [{'title': 'Laser wavelength (nm):', 'name': 'laser_wl', 'type': 'float', 'value': 515.},
              {'title': 'Fit options:', 'name': 'fit_options', 'type': 'group', 'children': [
                  {'title': 'Fit in?:', 'name': 'fit_units', 'type': 'list', 'value': 'nm', 'values': ['nm', 'cm-1', 'eV']},
                  {'title': 'Polynomial Fit order:', 'name': 'fit_order', 'type': 'int', 'value': 1, 'min': 1, 'max':3},
                  {'title': 'Do calib:', 'name': 'do_calib', 'type': 'bool', 'value': False},

              ]},
              {'title': 'Peaks', 'name': 'peaks_table', 'type': 'table_view'},
              PeakGroup(title='Peak options:', name="peak_options", channels=[]),
              ]

    def __init__(self, parent):
        QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
        super().__init__()
        if not isinstance(parent, DockArea):
            raise Exception('no valid parent container, expected a DockArea')
        self.dockarea = parent
        self.window = self.dockarea.parent()

        self.setupUI()

        self.raw_datas = dict([])
        self.raw_axis = None
        self.text_peak_items = []
        self.arrow_peak_items = []
        self.table_model = None
        self.calib_plot = None
        self.filenames = []

    def create_toolbar(self):
        self.toolbar.addWidget(QtWidgets.QLabel('Calibration:'))

        iconadd = QtGui.QIcon()
        iconadd.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/Add2.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
        self.addh5_action = QtWidgets.QAction(iconadd, "Add spectrum", None)
        self.toolbar.addAction(self.addh5_action)
        self.addh5_action.triggered.connect(self.add_spectrum_h5)

        iconreset = QtGui.QIcon()
        iconreset.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/Refresh2.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
        self.reset_action = QtWidgets.QAction(iconreset, "Remove plots", None)
        self.toolbar.addAction(self.reset_action)
        self.reset_action.triggered.connect(self.reset)


    def add_spectrum_h5(self):
        data, fname, node_path = browse_data(ret_all=True)
        if data is not None:
            file = Path(fname).parts[-1]
            self.filenames.append(file)
            self.raw_datas[file] = data
            self.raw_axis = np.linspace(0, len(data) - 1, len(data))

            # with tables.open_file(fname) as h5file:
            #     data_node = h5file.get_node(node_path)
            #
            #
            #     if 'X_axis' in list(data_node._v_parent._v_children):
            #         self.raw_axis = data_node._v_parent._f_get_child('X_axis').read()

            self.viewer_data.show_data(self.raw_datas.values(), x_axis=self.raw_axis, labels=self.filenames)



    def update_peak_source(self):
        for child in self.settings.child(('peak_options')).children():
            child.child(('channel')).setOpts(limits=self.filenames)


    def reset(self):
        self.raw_datas = dict([])
        self.raw_axis = None

        self.viewer_data.remove_plots()


    def setupUI(self):
        horlayout = QtWidgets.QHBoxLayout()
        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        self.setLayout(horlayout)
        horlayout.addWidget(splitter)

        tab = QtWidgets.QTabWidget()
        

        form = QtWidgets.QWidget()
        self.viewer_data = Viewer1D(form)
        self.plot_peak_item = self.viewer_data.viewer.plotwidget.plot()

        form1 = QtWidgets.QWidget()
        self.viewer_calib = Viewer1D(form1)
        self.viewer_calib.set_axis_label(axis_settings=dict(orientation='left',label='Photon wavelength',units='nm'))

        tab.addTab(form, 'Data Viewer')
        tab.addTab(form1, 'Calibration')


        splitter.addWidget(tab)

        self.settings = Parameter.create(name='settings', type='group', children=self.params)
        self.settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

        self.settings_tree = ParameterTree()
        self.settings_tree.setMinimumWidth(300)
        self.settings_tree.setParameters(self.settings, showTop=False)

        splitter.addWidget(self.settings_tree)

        # creating a toolbar
        self.toolbar = QtWidgets.QToolBar()
        self.create_toolbar()
        self.window.addToolBar(self.toolbar)

    def parameter_tree_changed(self, param, changes):
        for param, change, data in changes:
            path = self.settings.childPath(param)
            if path is not None:
                childName = '.'.join(path)
            else:
                childName = param.name()
            if change == 'childAdded':
                self.update_peak_source()
                if param.name() == 'peak_options':
                    QtWidgets.QApplication.processEvents()
                    #self.update_peak_finding()

            elif change == 'value':
                if param.name() in custom_tree.iter_children(self.settings.child(('peak_options')), []):
                    self.update_peak_finding()

                elif param.name() == 'fit_units':
                    if self.table_model is not None:
                        self.table_model.setHeaderData(2, Qt.Horizontal, data)

                if self.settings.child('fit_options', 'do_calib').value():
                    self.calculate_calibration(self.settings.child(('peaks_table')).value()._data)

            elif change == 'parent':
                pass

    def update_peak_finding(self):
        try:
            if len(self.raw_datas) != 0:
                peak_options = []

                for channel in self.filenames:

                    opts = dict([])
                    for child in self.settings.child(('peak_options')):
                        if child.child(('channel')).value() == channel:
                            children = [ch.name() for ch in child.children() if not(ch.name() =='use_opts' or ch.name() =='channel')]
                            if child.child(('use_opts')).value():
                                param_opt = child.child((children[0]))
                                opts[param_opt.name()] = param_opt.value()
                    if len(opts) != 0:
                        peak_options.append(dict(channel=channel, opts=opts))

                self.peak_indexes = []
                self.peak_amplitudes = []
                if len(peak_options) != 0:
                    for option in peak_options:
                        peak_indexes, properties = find_peaks(self.raw_datas[option['channel']], **option['opts'])
                        self.peak_indexes.extend(list(peak_indexes))
                        self.peak_amplitudes.extend(list(self.raw_datas[option['channel']][peak_indexes]))

                    self.peak_indexes = np.array(self.peak_indexes)
                    self.peak_amplitudes = np.array(self.peak_amplitudes)

                    arg_sorted_indexes = np.argsort(self.peak_indexes)

                    self.peak_indexes = self.peak_indexes[arg_sorted_indexes]
                    self.peak_amplitudes = self.peak_amplitudes[arg_sorted_indexes]


                    if len(self.peak_indexes) != 0:
                        self.viewer_data.viewer.plotwidget.plotItem.removeItem(self.plot_peak_item)
                        while len(self.text_peak_items) != 0:
                            self.viewer_data.viewer.plotwidget.plotItem.removeItem(self.text_peak_items.pop(0))
                            self.viewer_data.viewer.plotwidget.plotItem.removeItem(self.arrow_peak_items.pop(0))

                        self.plot_peak_item = self.viewer_data.viewer.plotwidget.plot(self.raw_axis[self.peak_indexes], self.peak_amplitudes, pen=None, symbol='+')

                        for ind, peak_index in enumerate(self.peak_indexes):
                            item = TextItem('({:.00f},{:.02f})'.format(self.raw_axis[peak_index], self.peak_amplitudes[ind]), angle=45, color='w', anchor=(0,1))
                            size = self.viewer_data.viewer.plotwidget.plotItem.vb.itemBoundingRect(item)
                            item.setPos(self.raw_axis[peak_index], self.peak_amplitudes[ind]+size.height())
                            self.text_peak_items.append(item)

                            item_ar = ArrowItem(pos=(self.raw_axis[peak_index], self.peak_amplitudes[ind] + size.height() / 5),
                                                angle=-90, tipAngle=30, baseAngle=20,
                                      headLen=10, tailLen=20, tailWidth=1, pen=None, brush='w')
                            self.arrow_peak_items.append(item_ar)
                            self.viewer_data.viewer.plotwidget.plotItem.addItem(item)
                            self.viewer_data.viewer.plotwidget.plotItem.addItem(item_ar)

                        self.table_model = PandasModel(pd.DataFrame([[False, ind, 0] for ind in self.peak_indexes],
                                        columns=['Use', 'Pxl', self.settings.child('fit_options', 'fit_units').value()]))
                        self.settings.child(('peaks_table')).setValue(self.table_model)
        except Exception as e:
            logger.exception(str(e))

    def update_status(self,txt, log_type=None):
        """

        """
        print(txt)
        if log_type is not None:
            self.log_signal.emit(txt)

    def calculate_calibration(self, dataframe = pd.DataFrame()):
        try:
            data_to_use = dataframe.query('Use == True')
            data = data_to_use[dataframe.columns[2]].to_numpy()
            indexes = data_to_use['Pxl'].to_numpy()

            unit = self.settings.child('fit_options', 'fit_units').value()
            if unit == 'nm':
                pass
            elif unit == 'cm-1':
                data = Ecmrel2Enm(data, self.settings.child(('laser_wl')).value())
            elif unit == 'eV':
                data = eV2nm(data)

            if data.size != 0:
                if self.calib_plot is not None:
                    self.viewer_calib.viewer.plotwidget.plotItem.removeItem(self.calib_plot)
                self.calib_plot = self.viewer_calib.viewer.plotwidget.plot(indexes, data, pen=None, symbol='+')

                calib_coeffs = np.polyfit(indexes-np.max(self.raw_axis)/2, data, self.settings.child('fit_options', 'fit_order').value())
                calib_data = np.polyval(calib_coeffs, self.raw_axis-np.max(self.raw_axis)/2)

                self.viewer_calib.show_data([calib_data],
                            labels=['Fit of order {:d}'.format(self.settings.child('fit_options', 'fit_order').value())])

                self.coeffs_calib.emit(list(calib_coeffs)[::-1])

        except Exception as e:
            self.update_status(e, 'log')








if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = QtWidgets.QMainWindow()
    area = DockArea()
    win.setCentralWidget(area)
    win.resize(1000, 500)
    win.setWindowTitle('Calibration')
    dock = Dock('Calibration')
    area.addDock(dock)
    prog = Calibration(area)
    dock.addWidget(prog)
    win.show()
    prog.add_spectrum_h5()

    sys.exit(app.exec_())