import sys
import tables
from collections import OrderedDict
import datetime
import os
import numpy as np
from copy import deepcopy
from qtpy import QtGui, QtWidgets
from qtpy.QtCore import QObject, Slot, Signal, QLocale, QDateTime, QRectF, QDate, QThread, Qt
from pathlib import Path
import pickle
from pyqtgraph.dockarea import Dock
from pymodaq.daq_utils.gui_utils import DockArea, select_file
from pyqtgraph.parametertree import Parameter, ParameterTree
import pyqtgraph.parametertree.parameterTypes as pTypes
import pymodaq.daq_utils.custom_parameter_tree as custom_tree
from pymodaq.daq_utils.daq_utils import Enm2cmrel, Ecmrel2Enm, nm2eV, eV2nm, eV2radfs, l2w, getLineInfo, ThreadCommand
from pymodaq.daq_utils.plotting.qled import QLED
from pymodaq.daq_viewer.daq_viewer_main import DAQ_Viewer
from pymodaq.daq_utils.plotting.viewer1D.viewer1D_main import Viewer1D
from pymodaq.daq_utils import daq_utils as utils
from pymodaq.daq_utils.h5modules import H5Browser, H5Saver, browse_data, H5BrowserUtil
from pymodaq_spectro.utils.calibration import Calibration
from pymodaq.dashboard import DashBoard
from units_converter.main import UnitsConverter

import logging

logger = utils.set_logger(utils.get_module_name(__file__))
spectro_path = utils.get_set_config_path('spectrometer_configs')


class Spectrometer(QObject):
    """
    Defines a Spectrometer object, unified interface for many spectrometers

    Parameters that could be set in the selected detector plugin (should be defined there):
    'laser_wl' : value of the configured laser (could eventually be changed, case of Xplora, Labram...)
    'spectro_center_freq': value of the configured grating center wavelength (could eventually be changed, case of Shamrock, Xplora...)


    """
    #custom signal that will be fired sometimes. Could be connected to an external object method or an internal method
    log_signal = Signal(str)

    #list of dicts enabling the settings tree on the user interface
    params = [{'title': 'Configuration settings:', 'name': 'config_settings', 'type': 'group', 'children': [
                        {'title': 'Laser wavelength (nm):', 'name': 'laser_wl', 'type': 'float', 'value': 515.},
                        {'title': 'Laser wavelength (nm):', 'name': 'laser_wl_list', 'type': 'list', 'limits':['']},
                        {'title': 'Current Detector:', 'name': 'curr_det', 'type': 'str', 'value': ''},
                        {'title': 'Show detector:', 'name': 'show_det', 'type': 'bool', 'value': False},
                        ],},
              {'title': 'Calibration settings:', 'name': 'calib_settings', 'type': 'group', 'children': [
                  {'title': 'Use calibration:', 'name': 'use_calib', 'type': 'bool', 'value': False},
                  {'title': 'Save calibration', 'name': 'save_calib', 'type': 'bool_push', 'value': False},
                  {'title': 'Load calibration', 'name': 'load_calib', 'type': 'bool_push', 'value': False},
                  {'title': 'Calibration coeffs:', 'name': 'calib_coeffs', 'type': 'group', 'children': [
                      {'title': 'Center wavelength (nm):', 'name': 'center_calib', 'type': 'float', 'value': 515.},
                      {'title': 'Slope (nm/pxl):', 'name': 'slope_calib', 'type': 'float', 'value': 1.},
                      {'title': 'Second order :', 'name': 'second_calib', 'type': 'float', 'value': 0},
                      {'title': 'third:', 'name': 'third_calib', 'type': 'float', 'value': 0},]},
                  {'title': 'Perform calibration:', 'name': 'do_calib', 'type': 'bool', 'value': False},

                     ]},
              {'title': 'Acquisition settings:', 'name': 'acq_settings', 'type': 'group', 'children': [
                  {'title': 'Spectro. Center:', 'name': 'spectro_center_freq', 'type': 'float', 'value': 800,},
                  {'title': 'Spectro. Center:', 'name': 'spectro_center_freq_txt', 'type': 'str', 'value': '????', 'readonly':True },
                  {'title': 'Units:', 'name': 'units', 'type': 'list', 'value': 'nm', 'limits': ['nm', 'cm-1', 'eV']},
                  {'title': 'Exposure (ms):', 'name': 'exposure_ms', 'type': 'float', 'value': 100, },
              ]},
              ]


    def __init__(self, parent):
        QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
        super().__init__()
        if not isinstance(parent, DockArea):
            raise Exception('no valid parent container, expected a DockArea')

        self.wait_time = 2000 #ms
        self.offline = True
        self.dockarea = parent
        self.mainwindow = parent.parent()
        self.spectro_widget = QtWidgets.QWidget()
        self.data_dict = None
        """
        List of the possible plugins that could be used with Spectrometer module
        type : dimensionality of the detector
        name: name of the plugin
        calib = True means there is a builtin calibration of the frequency axis
        movable : tells if the dispersion can be set (for instance by moving a grating)
        unit: valid only if calib is True. Unit of the calibration axis (x_axis of the detector), most often in
              nanometers. Possible values are 'nm', 'radfs' (rad/femtosecond), 'eV'
        laser: if False,  laser cannot be changed by the program, do it manually
        laser_list: if laser is True, laser_list gives a list of selectable lasers
        
        """

        self.current_det = None  # will be after initialization

        self.laser_set_manual = True

        #init the object parameters
        self.detector = None
        self.save_file_pathname = None
        self._spectro_wl = 550 # center wavelngth of the spectrum
        self.viewer_freq_axis = utils.Axis(data=None, label='Photon energy', units='')
        self.raw_data = []

        #init the user interface
        self.dashboard = self.set_dashboard()
        self.dashboard.preset_loaded_signal.connect(lambda: self.show_detector(False))
        self.dashboard.preset_loaded_signal.connect(self.set_detector)
        self.dashboard.preset_loaded_signal.connect(self.initialized)
        self.set_GUI()
        self.dashboard.new_preset_created.connect(lambda: self.create_menu(self.menubar))

        self.show_detector(False)
        self.dockarea.setEnabled(False)

    def set_dashboard(self):
        params = [{'title': 'Spectro Settings:', 'name': 'spectro_settings', 'type': 'group', 'children': [
            {'title': 'Is calibrated?', 'name': 'iscalibrated', 'type': 'bool', 'value': False, 'tooltip':
                'Whether the selected plugin has internal frequency calibration or not.'},
            {'title': 'Movable?', 'name': 'ismovable', 'type': 'bool', 'value': False, 'tooltip':
                'Whether the selected plugin has a functionality to change its central frequency: as a movable grating'
                ' for instance.'},
            {'title': 'Laser selectable?', 'name': 'laser_selectable', 'type': 'bool', 'value': False, 'tooltip':
                'Whether the selected plugin has a functionality to change its excitation ray'},
            {'title': 'Laser ray:', 'name': 'laser_ray', 'type': 'list', 'value': '', 'show_pb': True, 'tooltip':
                'List of settable laser rays (not manual ones)'},]},
        ]
        dashboard = DashBoard(self.dockarea.addTempArea())
        dashboard.set_preset_path(spectro_path)
        options =[dict(path='saving_options', options_dict=dict(visible=False)),
                  dict(path='use_pid', options_dict=dict(visible=False)),
                  dict(path='Moves', options_dict=dict(visible=False))]
        dashboard.set_extra_preset_params(params, options)

        dashboard.dockarea.window().setVisible(False)
        return dashboard

    def set_GUI(self):
        ###########################################
        ###########################################
        #init the docks containing the main widgets

        #######################################################################################################################
        #create a dock containing a viewer object, displaying the data for the spectrometer
        self.dock_viewer = Dock('Viewer dock', size=(350, 350))
        self.dockarea.addDock(self.dock_viewer, 'left')
        target_widget = QtWidgets.QWidget()
        self.viewer = Viewer1D(target_widget)
        self.dock_viewer.addWidget(target_widget)


        ################################################################
        #create a logger dock where to store info senf from the programm
        self.dock_logger = Dock("Logger")
        self.logger_list = QtWidgets.QListWidget()
        self.logger_list.setMinimumWidth(300)
        self.dock_logger.addWidget(self.logger_list)
        self.dockarea.addDock(self.dock_logger, 'right')
        self.log_signal[str].connect(self.add_log)



        ############################################
        # creating a menubar
        self.menubar = self.mainwindow.menuBar()
        self.create_menu(self.menubar)

        #creating a toolbar
        self.toolbar = QtWidgets.QToolBar()
        self.create_toolbar()
        self.mainwindow.addToolBar(self.toolbar)

        #creating a status bar
        self.statusbar = QtWidgets.QStatusBar()
        self.statusbar.setMaximumHeight(25)

        self.status_laser = QtWidgets.QLabel('????')
        self.status_laser.setAlignment(Qt.AlignCenter)
        #self.status_laser.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        #self.status_laser.setReadOnly(True)
        self.status_laser.setMaximumWidth(80)
        self.status_laser.setMinimumWidth(80)
        self.status_laser.setToolTip('Current laser wavelength')
        self.status_laser.setStyleSheet("background-color: red")

        self.status_center = QtWidgets.QLabel('????')
        self.status_center.setAlignment(Qt.AlignCenter)
        #self.status_center.setReadOnly(True)
        #self.status_center.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.status_center.setMaximumWidth(80)
        self.status_center.setMinimumWidth(80)
        self.status_center.setToolTip('center frequency of the spectrum, either in nm or cm-1')
        self.status_center.setStyleSheet("background-color: red")

        self.status_init = QLED()
        self.status_init.setToolTip('Initialization state of the detector')
        self.status_init.set_as_false()
        self.status_init.clickable = False

        self.statusbar.addPermanentWidget(self.status_laser)
        self.statusbar.addPermanentWidget(self.status_center)
        self.statusbar.addPermanentWidget(self.status_init)
        self.dockarea.window().setStatusBar(self.statusbar)

        #############################################
        self.settings = Parameter.create(name='settings', type='group', children=self.params)
        self.settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

        dock_config_settings = Dock('Configuration', size=(300, 350))
        self.dockarea.addDock(dock_config_settings, 'above', self.dock_logger)
        # create main parameter tree
        self.config_settings_tree = ParameterTree()
        dock_config_settings.addWidget(self.config_settings_tree, 10)
        self.config_settings_tree.setMinimumWidth(300)
        self.config_settings_tree.setParameters(self.settings.child(('config_settings')), showTop=False)
        #any change to the tree on the user interface will call the parameter_tree_changed method where all actions will be applied

        dock_calib_settings = Dock('Calibration', size=(300, 350))
        self.dockarea.addDock(dock_calib_settings, 'above', self.dock_logger)
        # create main parameter tree
        self.calib_settings_tree = ParameterTree()
        dock_calib_settings.addWidget(self.calib_settings_tree, 10)
        self.calib_settings_tree.setMinimumWidth(300)
        self.calib_settings_tree.setParameters(self.settings.child(('calib_settings')), showTop=False)
        #any change to the tree on the user interface will call the parameter_tree_changed method where all actions will be applied



        #this one for the custom application settings
        dock_acq_settings = Dock('Acquisition', size=(300, 350))
        self.dockarea.addDock(dock_acq_settings, 'above', dock_config_settings)
        # create main parameter tree
        self.acq_settings_tree = ParameterTree()
        dock_acq_settings.addWidget(self.acq_settings_tree, 10)
        self.acq_settings_tree.setMinimumWidth(300)
        self.acq_settings_tree.setParameters(self.settings.child(('acq_settings')), showTop=False)


    @Slot(ThreadCommand)
    def cmd_from_det(self,status):
        try:
            if status.command == 'spectro_wl':
                self.status_center.setStyleSheet("background-color: green")
                self.spectro_wl_is(status.attributes[0])

            elif status.command == 'laser_wl':
                #self.laser_set_manual = False
                self.settings.child('config_settings', 'laser_wl_list').setValue(status.attributes[0])
                self.status_laser.setText('{:}nm'.format(status.attributes[0]))
                self.status_laser.setStyleSheet("background-color: green")
                self.update_center_frequency(self.spectro_wl)


            elif status.command == 'exposure_ms':
                self.settings.child('acq_settings', 'exposure_ms').setValue(status.attributes[0])

            elif status.command == "x_axis":
                x_axis = status.attributes[0]
                if np.any(x_axis['data'] != self.viewer_freq_axis['data']) and self.current_det['calib']:
                    self.viewer_freq_axis.update(x_axis)
                    self.update_axis()

        except Exception as e:
            logger.exception(str(e))

    def update_status(self, txt, wait_time=1000, log_type=None):
        """

        """
        self.statusbar.showMessage(txt,wait_time)
        if log_type is not None:
            self.log_signal.emit(txt)


    def set_detector(self):

        self.detector = self.dashboard.detector_modules[0]
        self.settings.child('config_settings', 'curr_det').setValue(
            f"{self.detector.settings.child('main_settings','DAQ_type').value()} / "
            f"{self.detector.settings.child('main_settings','detector_type').value()} / {self.detector.title}")
        self.detector.custom_sig[ThreadCommand].connect(self.cmd_from_det)
        self.current_det = \
            dict(laser=self.dashboard.preset_manager.preset_params.child('spectro_settings', 'laser_selectable').value(),
                 laser_list=self.dashboard.preset_manager.preset_params.child('spectro_settings', 'laser_ray').opts['limits'],
                 movable=self.dashboard.preset_manager.preset_params.child('spectro_settings', 'ismovable').value(),
                 calib=self.dashboard.preset_manager.preset_params.child('spectro_settings', 'iscalibrated').value(),
                 )

        self.detector.grab_done_signal.connect(self.show_data)

        self.settings.sigTreeStateChanged.disconnect(self.parameter_tree_changed)
        if self.current_det['laser']:
            self.settings.child('config_settings', 'laser_wl_list').show()
            self.settings.child('config_settings', 'laser_wl').hide()
            self.settings.child('config_settings', 'laser_wl_list').setOpts(limits=self.current_det['laser_list'])
        else:
            self.settings.child('config_settings', 'laser_wl').show()
            self.settings.child('config_settings', 'laser_wl_list').hide()
        self.settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

        #apply current detector particularities
        #self.settings.child('acq_settings', 'spectro_center_freq').setOpts(readonly=not self.current_det['movable'])
        self.get_spectro_wl()
        QtWidgets.QApplication.processEvents()


        self.get_laser_wl()
        QtWidgets.QApplication.processEvents()

        self.get_exposure_ms()
        QtWidgets.QApplication.processEvents()

    def get_exposure_ms(self):
        self.detector.command_detector.emit(ThreadCommand('get_exposure_ms'))

    def set_exposure_ms(self, data):
        self.detector.command_detector.emit(ThreadCommand('set_exposure_ms', [data]))

    @Slot(bool)
    def initialized(self, state, offline=False):
        self.offline = offline
        self.grab_action.setEnabled(state)
        self.snap_action.setEnabled(state)
        if state or offline:
            self.status_init.set_as_true()
            self.dockarea.setEnabled(True)
        else:
            self.status_init.set_as_false()

    def update_center_frequency(self, spectro_wl):
        self._spectro_wl = spectro_wl
        if self.settings.child('acq_settings', 'units').value() == 'nm':
            self.settings.child('acq_settings', 'spectro_center_freq').setValue(spectro_wl)
        elif self.settings.child('acq_settings', 'units').value() == 'cm-1':
            self.settings.child('acq_settings', 'spectro_center_freq').setValue(Enm2cmrel(spectro_wl,
                                                                                          self.settings.child(
                                                                                              'config_settings',
                                                                                              'laser_wl').value()))
        elif self.settings.child('acq_settings', 'units').value() == 'eV':
            self.settings.child('acq_settings', 'spectro_center_freq').setValue(nm2eV(spectro_wl))

        self.set_status_center(self.settings.child('acq_settings', 'spectro_center_freq').value(),
                               self.settings.child('acq_settings', 'units').value())

    def set_status_center(self, val, unit, precision=3):
        self.status_center.setText(f'{val:.{precision}f} {unit}')

    def spectro_wl_is(self, spectro_wl):
        """
        this slot receives a signal from the detector telling it what's the current spectro_wl
        Parameters
        ----------
        spectro_wl
        """
        self._spectro_wl = spectro_wl
        self.update_center_frequency(spectro_wl)


    def set_spectro_wl(self, spectro_wl):
        try:
            if self.current_det['movable']:
                self.detector.command_detector.emit(ThreadCommand('set_spectro_wl', [spectro_wl]))
        except Exception as e:
            logger.exception(str(e))

    def get_spectro_wl(self):
        if self.current_det['calib']:
            self.settings.child('acq_settings', 'spectro_center_freq').show()
            self.settings.child('acq_settings', 'spectro_center_freq_txt').hide()
            self.detector.command_detector.emit(ThreadCommand('get_spectro_wl'))
            self.detector.command_detector.emit(ThreadCommand('get_axis'))
        else:
            self.settings.child('acq_settings', 'spectro_center_freq').hide()
            self.settings.child('acq_settings', 'spectro_center_freq_txt').show()
            self.viewer_freq_axis['units'] = 'Pxls'

    def get_laser_wl(self):
        if self.current_det['laser']:
            self.detector.command_detector.emit(ThreadCommand('get_laser_wl'))
        else:
            self.settings.child('config_settings', 'laser_wl').setValue(0)
    @property
    def spectro_wl(self):
        # try to get the param value from detector (if it has been added in the plugin)
        return self._spectro_wl

    @spectro_wl.setter
    def spectro_wl(self, spec_wl):
        # try to get the param value from detector (if it has been added in the plugin)
        self.set_spectro_wl(spec_wl)


    def show_detector(self, show=True):
        self.dashboard.mainwindow.setVisible(show)
        for area in self.dashboard.dockarea.tempAreas:
            area.window().setVisible(show)



    def parameter_tree_changed(self, param, changes):
        for param, change, data in changes:
            path = self.settings.childPath(param)
            if path is not None:
                childName = '.'.join(path)
            else:
                childName = param.name()
            if change == 'childAdded':
                pass

            elif change == 'value':
                if param.name() == 'show_det':
                    self.show_detector(data)

                elif param.name() == 'spectro_center_freq':
                    unit = self.settings.child('acq_settings', 'units').value()
                    if unit == 'nm':
                        center_wavelength = data
                    elif unit == 'cm-1':
                        center_wavelength = Ecmrel2Enm(data, self.settings.child( 'config_settings', 'laser_wl').value())
                    elif unit == 'eV':
                        center_wavelength = eV2nm(data)

                    if int(self.spectro_wl*100) != int(100*center_wavelength): #comprison at 1e-2
                        self.spectro_wl = center_wavelength

                    self.update_axis()



                elif param.name() == 'units':
                    if self.settings.child('acq_settings', 'spectro_center_freq').value() > 0.000000001:
                        if data == 'nm':
                            self.settings.child('acq_settings', 'spectro_center_freq').setValue(self._spectro_wl)
                        elif data == 'cm-1':
                            self.settings.child('acq_settings', 'spectro_center_freq').setValue(Enm2cmrel(self._spectro_wl,
                                                    self.settings.child( 'config_settings', 'laser_wl').value()))
                        elif data == 'eV':
                            self.settings.child('acq_settings', 'spectro_center_freq').setValue(nm2eV(self._spectro_wl))

                        self.set_status_center(self.settings.child('acq_settings', 'spectro_center_freq').value(),
                                               self.settings.child('acq_settings', 'units').value())

                elif param.name() == 'laser_wl_list':
                    if data is not None:
                        self.move_laser_wavelength(data)

                elif param.name() == 'laser_wl':
                    if data is not None:
                        self.move_laser_wavelength(data)
                        if int(data) == 0:
                            self.settings.child('acq_settings', 'units').setValue('nm')
                            self.settings.child('acq_settings', 'units').setOpts(readonly=True)
                        else:
                            self.settings.child('acq_settings', 'units').setOpts(readonly=False)
                        if data != 0:
                            self.set_manual_laser_wl(data)



                elif param.name() == 'exposure_ms':
                    self.set_exposure_ms(data)

                elif param.name() == 'do_calib':
                    if len(self.raw_data) != 0:
                        if data:
                            self.calib_dock = Dock('Calibration module')
                            self.dockarea.addDock(self.calib_dock)
                            self.calibration = Calibration(self.dockarea)
                            self.calib_dock.addWidget(self.calibration)

                            self.calibration.coeffs_calib.connect(self.update_calibration)
                        else:
                            self.calib_dock.close()

                elif param.name() == 'save_calib':
                    filename = select_file(start_path=self.save_file_pathname, save=True, ext='xml')
                    if filename != '':
                        custom_tree.parameter_to_xml_file(self.settings.child('calib_settings', 'calib_coeffs'), filename)

                elif param.name() == 'load_calib':
                    filename = select_file(start_path=self.save_file_pathname, save=False, ext='xml')
                    if filename != '':
                        children = custom_tree.XML_file_to_parameter(filename)
                        self.settings.child('calib_settings', 'calib_coeffs').restoreState(
                            Parameter.create(title='Calibration coeffs:', name='calib_coeffs', type='group',
                                             children=children).saveState())



                elif param.name() in custom_tree.iter_children(self.settings.child('calib_settings', 'calib_coeffs')) \
                        or param.name() == 'use_calib':
                    if self.settings.child('calib_settings', 'use_calib').value():
                        calib_coeffs = [self.settings.child('calib_settings', 'calib_coeffs', 'third_calib').value(),
                                        self.settings.child('calib_settings', 'calib_coeffs', 'second_calib').value(),
                                        self.settings.child('calib_settings', 'calib_coeffs', 'slope_calib').value(),
                                        self.settings.child('calib_settings', 'calib_coeffs', 'center_calib').value()]

                        self.update_center_frequency(self.settings.child('calib_settings', 'calib_coeffs', 'center_calib').value())
                        self.settings.child('acq_settings', 'spectro_center_freq').show()
                        self.settings.child('acq_settings', 'spectro_center_freq').setOpts(readonly=True)
                        self.status_center.setStyleSheet("background-color: green")
                        self.settings.child('acq_settings', 'spectro_center_freq_txt').hide()
                        x_axis_pxls = np.linspace(0, self.raw_data[0].size-1, self.raw_data[0].size)
                        self.viewer_freq_axis['data'] = np.polyval(calib_coeffs, x_axis_pxls-np.max(x_axis_pxls)/2)
                        self.update_axis()
                    else:
                        self.settings.child('acq_settings', 'spectro_center_freq').hide()
                        self.settings.child('acq_settings', 'spectro_center_freq_txt').show()
                        self.status_center.setStyleSheet("background-color: red")


            elif change == 'parent':
                pass

    @Slot(list)
    def update_calibration(self, coeffs):
        self.settings.child('calib_settings', 'calib_coeffs', 'center_calib').setValue(coeffs[0])
        self.settings.child('calib_settings', 'calib_coeffs', 'slope_calib').setValue(coeffs[1])
        if len(coeffs) > 2:
            self.settings.child('calib_settings', 'calib_coeffs', 'second_calib').setValue(coeffs[2])
        else:
            self.settings.child('calib_settings', 'calib_coeffs', 'second_calib').setValue(0)
        if len(coeffs) > 3:
            self.settings.child('calib_settings', 'calib_coeffs', 'third_calib').setValue(coeffs[3])
        else:
            self.settings.child('calib_settings', 'calib_coeffs', 'third_calib').setValue(0)


    def set_manual_laser_wl(self, laser_wl):
        messg = QtWidgets.QMessageBox()
        messg.setText('You manually changed the laser wavelength to {:}nm!'.format(laser_wl))
        messg.setInformativeText("Is that correct?")
        messg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        ret = messg.exec()
        if ret == QtWidgets.QMessageBox.Yes:
            self.status_laser.setText('{:}nm'.format(laser_wl))
            self.status_laser.setStyleSheet("background-color: green")
            self.settings.child('acq_settings', 'units').setOpts(readonly=False)


    def move_laser_wavelength(self, laser_wavelength):
        #do hardware stuff if possible (Mock, labspec...)
        try:
            if self.current_det['laser']:
                self.detector.command_detector.emit(ThreadCommand('set_laser_wl', [laser_wavelength]))
        except Exception as e:
            logger.exception(str(e))

    @Slot(OrderedDict)
    def show_data(self, data):
        """
        do stuff with data from the detector if its grab_done_signal has been connected
        Parameters
        ----------
        data: (OrderedDict) #OrderedDict(name=self.title,x_axis=None,y_axis=None,z_axis=None,data0D=None,data1D=None,data2D=None)
        """
        self.data_dict = data
        if 'data1D' in data:
            self.raw_data = []
            for key in data['data1D']:
                self.raw_data.append(data['data1D'][key]['data'])
                if 'x_axis' in data['data1D'][key]:
                    x_axis = data['data1D'][key]['x_axis']
                else:
                    x_axis = utils.Axis(
                        data=np.linspace(0, len(data['data1D'][key]['data'])-1, len(data['data1D'][key]['data'])),
                        units='pxls',
                        label='')
                if self.viewer_freq_axis['data'] is None:
                    self.viewer_freq_axis.update(x_axis)
                elif np.any(x_axis['data'] != self.viewer_freq_axis['data']) and self.current_det['calib']:
                    self.viewer_freq_axis.update(x_axis)

            self.viewer.show_data(self.raw_data)
            self.update_axis()



    def update_axis(self):
        axis = utils.Axis()
        unit = self.settings.child('acq_settings', 'units').value()
        if unit == 'nm':
            axis['data'] = self.viewer_freq_axis['data']
        elif unit == 'cm-1':
            axis['data'] = Enm2cmrel(self.viewer_freq_axis['data'],
                                     self.settings.child('config_settings', 'laser_wl').value())
        elif unit == 'eV':
            axis['data'] = nm2eV(self.viewer_freq_axis['data'])
        axis['units'] = unit
        axis['label'] = 'Photon energy'
        self.viewer.x_axis = axis


    def create_menu(self, menubar):
        """
        """
        menubar.clear()

        # %% create file menu
        file_menu = menubar.addMenu('File')
        load_action = file_menu.addAction('Load file')
        load_action.triggered.connect(self.load_file)
        save_action = file_menu.addAction('Save file')
        save_action.triggered.connect(self.save_data)
        export_action = file_menu.addAction('Export as ascii')
        export_action.triggered.connect(lambda: self.save_data(export=True))

        file_menu.addSeparator()
        file_menu.addAction('Show log file', self.show_log)
        file_menu.addSeparator()
        quit_action = file_menu.addAction('Quit')
        quit_action.triggered.connect(self.quit_function)

        settings_menu = menubar.addMenu('Settings')
        settings_menu.addAction('Show Units Converter', self.show_units_converter)
        docked_menu = settings_menu.addMenu('Docked windows')
        docked_menu.addAction('Load Layout', self.load_layout_state)
        docked_menu.addAction('Save Layout', self.save_layout_state)

        self.preset_menu = menubar.addMenu(self.dashboard.preset_menu)
        self.preset_menu.menu().addSeparator()
        self.preset_menu.menu().addAction('Offline Mode', lambda: self.initialized(state=False, offline=True))

    def load_layout_state(self, file=None):
        """
            Load and restore a layout state from the select_file obtained pathname file.

            See Also
            --------
            utils.select_file
        """
        try:
            if file is None:
                file = select_file(save=False, ext='dock')
            if file is not None:
                with open(str(file), 'rb') as f:
                    dockstate = pickle.load(f)
                    self.dockarea.restoreState(dockstate)
            file = file.name
            self.settings.child('loaded_files', 'layout_file').setValue(file)
        except Exception as e:
            logger.exception(str(e))

    def save_layout_state(self, file=None):
        """
            Save the current layout state in the select_file obtained pathname file.
            Once done dump the pickle.

            See Also
            --------
            utils.select_file
        """
        try:
            dockstate = self.dockarea.saveState()
            if 'float' in dockstate:
                dockstate['float'] = []
            if file is None:
                file = select_file(start_path=None, save=True, ext='dock')
            if file is not None:
                with open(str(file), 'wb') as f:
                    pickle.dump(dockstate, f, pickle.HIGHEST_PROTOCOL)
        except Exception as e:
            logger.exception(str(e))



    def show_log(self):
        import webbrowser
        webbrowser.open(logging.getLogger('pymodaq').handlers[0].baseFilename)

    def show_units_converter(self):
        self.units_converter = UnitsConverter()
        dock_converter = Dock('Units Converter', size=(300, 350))
        self.dockarea.addDock(dock_converter, 'bottom', self.dock_logger)
        dock_converter.addWidget(self.units_converter.parent)

    def load_file(self):
        data, fname, node_path = browse_data(ret_all=True)
        if data is not None:
            h5utils = H5BrowserUtil()
            h5utils.open_file(fname)
            data, axes, nav_axes, is_spread = h5utils.get_h5_data(node_path)
            data_node = h5utils.get_node(node_path)
            if data_node.attrs['type'] == 'data':
                if data_node.attrs['data_dimension'] == '1D':
                    data_dict = OrderedDict(data1D=dict(raw=dict(data=data, x_axis=axes['x_axis'])))
                    self.show_data(data_dict)
            h5utils.close_file()

    def quit_function(self):
        #close all stuff that need to be
        if self.detector is not None:
            self.detector.quit_fun()
            QtWidgets.QApplication.processEvents()
            self.mainwindow.close()

    def create_toolbar(self):
        self.toolbar.addWidget(QtWidgets.QLabel('Acquisition:'))

        iconquit = QtGui.QIcon()
        iconquit.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/close2.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
        self.quit_action = QtWidgets.QAction(iconquit, "Quit program", None)
        self.toolbar.addAction(self.quit_action)
        self.quit_action.triggered.connect(self.quit_function)

        iconload = QtGui.QIcon()
        iconload.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/Open.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.loadaction = QtWidgets.QAction(iconload, "Load target file (.h5, .png, .jpg) or data from camera", None)
        self.toolbar.addAction(self.loadaction)
        self.loadaction.triggered.connect(self.load_file)


        iconsave = QtGui.QIcon()
        iconsave.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/SaveAs.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
        self.saveaction = QtWidgets.QAction(iconsave, "Save current data", None)
        self.toolbar.addAction(self.saveaction)
        self.saveaction.triggered.connect(self.save_data)

        iconrun = QtGui.QIcon()
        iconrun.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/run2.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.grab_action = QtWidgets.QAction(iconrun, 'Grab', None)
        self.grab_action.setCheckable(True)
        self.toolbar.addAction(self.grab_action)
        self.grab_action.triggered.connect(self.grab_detector)

        iconsnap = QtGui.QIcon()
        iconsnap.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/snap.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.snap_action = QtWidgets.QAction(iconsnap, 'Snap', None)
        self.snap_action.triggered.connect(self.snap_detector)
        self.toolbar.addAction(self.snap_action)

        self.grab_action.setEnabled(False)
        self.snap_action.setEnabled(False)


    def grab_detector(self):
        self.detector.ui.grab_pb.click()

    def snap_detector(self):
        self.detector.ui.single_pb.click()


    def save_data(self, export=False):
        try:
            if export:
                ext = 'dat'
            else:
                ext = 'h5'
            path = select_file(start_path=self.save_file_pathname, save=True, ext=ext)
            if not (not(path)):
                if not export:
                    h5saver = H5Saver(save_type='detector')
                    h5saver.init_file(update_h5=True, custom_naming=False, addhoc_file_path=path)

                    settings_str = b'<All_settings>' + custom_tree.parameter_to_xml_string(self.settings)
                    if self.detector is not None:
                        settings_str += custom_tree.parameter_to_xml_string(self.detector.settings)
                        if hasattr(self.detector.ui.viewers[0], 'roi_manager'):
                            settings_str += custom_tree.parameter_to_xml_string(self.detector.ui.viewers[0].roi_manager.settings)
                    settings_str += custom_tree.parameter_to_xml_string(h5saver.settings)
                    settings_str += b'</All_settings>'

                    det_group = h5saver.add_det_group(h5saver.raw_group, "Data", settings_str)
                    try:
                        self.channel_arrays = OrderedDict([])
                        data_dim = 'data1D'
                        if not h5saver.is_node_in_group(det_group, data_dim):
                            self.channel_arrays['data1D'] = OrderedDict([])
                            data_group = h5saver.add_data_group(det_group, data_dim)
                            for ind_channel, data in enumerate(self.raw_data):  # list of numpy arrays
                                channel = f'CH{ind_channel:03d}'
                                channel_group = h5saver.add_CH_group(data_group, title=channel)

                                self.channel_arrays[data_dim]['parent'] = channel_group
                                self.channel_arrays[data_dim][channel] = h5saver.add_data(channel_group,
                                                                                          dict(data=data,
                                                                                               x_axis=self.viewer_freq_axis),
                                                                                          scan_type='',
                                                                                          enlargeable=False)
                        h5saver.close_file()
                    except Exception as e:
                        logger.exception(str(e))
                else:
                    data_to_save = [self.viewer_freq_axis['data']]
                    data_to_save.extend([dat for dat in self.raw_data])
                    np.savetxt(path, data_to_save, delimiter='\t')

        except Exception as e:
            logger.exception(str(e))


    @Slot(str)
    def add_log(self, txt):
        """
            Add a log to the logger list from the given text log and the current time

            ================ ========= ======================
            **Parameters**   **Type**   **Description**

             *txt*             string    the log to be added
            ================ ========= ======================

        """
        now = datetime.datetime.now()
        new_item = QtWidgets.QListWidgetItem(str(now) + ": " + txt)
        self.logger_list.addItem(new_item)
        ##to do
        ##self.save_parameters.logger_array.append(str(now)+": "+txt)

    @Slot(str)
    def emit_log(self, txt):
        """
            Emit a log-signal from the given log index

            =============== ======== =======================
            **Parameters**  **Type** **Description**

             *txt*           string   the log to be emitted
            =============== ======== =======================

        """
        self.log_signal.emit(txt)


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = QtWidgets.QMainWindow()
    area = DockArea()
    win.setCentralWidget(area)
    win.resize(1000, 500)
    win.setWindowTitle('pymodaq example')
    prog = Spectrometer(area)
    win.show()
    sys.exit(app.exec_())

