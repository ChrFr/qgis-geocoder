# -*- coding: utf-8 -*-
"""
/***************************************************************************
 BKGGeocoderDialog
                                 A QGIS plugin
 uses BKG geocoding API to geocode adresses
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                             -------------------
        begin                : 2018-10-19
        git sha              : $Format:%H$
        copyright            : (C) 2018 by GGR
        email                : franke@ggr-planung.de
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os

from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis import utils
from qgis.core import QgsCoordinateReferenceSystem
from qgis.gui import QgsProjectionSelectionWidget

from interface.dialogs import (OpenCSVDialog, SaveCSVDialog, ProgressDialog,
                               ReverseGeocodingDialog, FeaturePickerDialog)
from config import Config

config = Config()
UI_PATH = os.path.join(os.path.dirname(__file__), 'ui')


class MainWidget(QtWidgets.QDockWidget):
    ui_file = 'main_dockwidget.ui'
    closingWidget = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(MainWidget, self).__init__(parent)

        self.iface = utils.iface
        self.canvas = self.iface.mapCanvas()
        ui_file = self.ui_file if os.path.exists(self.ui_file) \
            else os.path.join(UI_PATH, self.ui_file)
        uic.loadUi(ui_file, self)
        self.setAllowedAreas(
            Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea
        )
        self.setupUi()

    def setupUi(self):
        self.importcsv_button.clicked.connect(self.import_csv)
        self.exportcsv_button.clicked.connect(self.export_csv)
        self.reversegeocoding_button.clicked.connect(self.reverse_geocode)
        self.featurepicker_button.clicked.connect(self.feature_picker)
        self.setup_config()

    def setup_config(self):
        self.search_and_check.setChecked(config.logic_link == 'AND')
        self.search_and_check.toggled.connect(
            lambda: setattr(config, 'logic_link', 'AND'))
        self.search_or_check.toggled.connect(
            lambda: setattr(config, 'logic_link', 'OR'))

        self.api_key_edit.setText(config.api_key)
        self.api_key_edit.editingFinished.connect(
            lambda: setattr(config, 'api_key', self.api_key_edit.text()))
        crs = QgsCoordinateReferenceSystem(config.projection)
        self.output_projection_select.setCrs(crs)

        self.output_projection_select.crsChanged.connect(
            lambda crs: setattr(config, 'projection', crs.authid()))

        self.selected_features_only_check.setChecked(
            config.selected_features_only)
        self.selected_features_only_check.toggled.connect(
            lambda: setattr(config, 'selected_features_only',
                            self.selected_features_only_check.isChecked()))

    def feature_picker(self):
        dialog = FeaturePickerDialog(parent=self)
        dialog.show()

    def reverse_geocode(self):
        dialog = ReverseGeocodingDialog(parent=self)
        dialog.show()

    def geocode(self):
        dialog = ProgressDialog(parent=self)
        dialog.show()

    def import_csv(self):
        dialog = OpenCSVDialog(parent=self)
        dialog.show()

    def export_csv(self):
        dialog = SaveCSVDialog(parent=self)
        dialog.show()

    def closeEvent(self, event):
        self.closingWidget.emit()
        event.accept()

    def show(self):
        self.iface.addDockWidget(
            Qt.LeftDockWidgetArea,
            self
        )
        self.setFloating(True);
        self.resize(self.sizeHint().width(),
                    self.sizeHint().height())
        geometry = self.geometry()
        self.setGeometry(500, 500,
                         geometry.width(), geometry.height())
