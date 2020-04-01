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
from qgis.PyQt.QtCore import pyqtSignal, Qt, QVariant
from qgis import utils
from qgis.core import QgsCoordinateReferenceSystem, QgsField
from qgis.PyQt.QtWidgets import (QHBoxLayout, QLabel, QComboBox,
                                 QCheckBox, QLineEdit, QInputDialog,
                                 QMessageBox)

from interface.dialogs import (OpenCSVDialog, SaveCSVDialog, ProgressDialog,
                               ReverseGeocodingDialog, FeaturePickerDialog)
from geocoder.bkg_geocoder import BKGGeocoder
from geocoder.geocoder import Geocoding
from config import Config

config = Config()
UI_PATH = os.path.join(os.path.dirname(__file__), 'ui')

BKG_FIELDS = [
    ('bkg_feature_id', QVariant.Int, 'int4'),
    ('bkg_n_results', QVariant.Int, 'int2'),
    ('bkg_i', QVariant.Double, 'int2'),
    ('bkg_typ', QVariant.String, 'text'),
    ('bkg_text', QVariant.String, 'text'),
    ('bkg_score', QVariant.Double, 'float8')
]

def clear_layout(layout):
    while layout.count():
        child = layout.takeAt(0)
        if not child:
            continue
        if child.widget():
            child.widget().deleteLater()
        elif child.layout() is not None:
            clear_layout(child.layout())


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
        self.geocoder = BKGGeocoder(config.api_key, srs=config.projection)
        self.setupUi()

    def setupUi(self):
        self.importcsv_button.clicked.connect(self.import_csv)
        self.exportcsv_button.clicked.connect(self.export_csv)
        self.reversegeocoding_button.clicked.connect(self.reverse_geocode)
        self.featurepicker_button.clicked.connect(self.feature_picker)
        self.request_start_button.clicked.connect(self.geocode)
        # ToDo: set filters
        self.layer_combo.layerChanged.connect(self.register_layer)

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
        self.iface.addDockWidget(Qt.LeftDockWidgetArea, self)
        self.setFloating(True);
        self.resize(self.sizeHint().width(), self.sizeHint().height())
        geometry = self.geometry()
        self.setGeometry(500, 500, geometry.width(), geometry.height())

    def log(self, text, color='black'):
        self.log_edit.insertHtml(
            f'<span style="color: {color}">{text}</span><br>')
        scrollbar = self.log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_result(self, feature, results):
        print(results)

    def register_layer(self, layer):
        '''
        add field checks depending on given layer to UI and preset
        layer related UI elements
        '''
        bkg_f = [f[0] for f in BKG_FIELDS]
        if not layer:
            return
        self.geocoding = Geocoding(layer, self.geocoder, ignore=bkg_f)
        self.geocoding.message.connect(self.log)
        self.geocoding.progress.connect(self.progress_bar.setValue)
        self.geocoding.feature_done.connect(self.set_result)
        self.geocoding.error.connect(lambda msg: self.log(msg, color='red'))
        #self.geocoding.finished.connect(lambda success: self.progress_bar)

        # remove old widgets
        clear_layout(self.parameter_grid)

        for i, field_name in enumerate(self.geocoding.fields()):
            checkbox = QCheckBox()
            checkbox.setText(field_name)
            combo = QComboBox()
            combo.addItem('unspezifisch oder nicht aufgeführte Kombination', None)
            for key, text in self.geocoder.keywords.items():
                combo.addItem(text, key)

            def checkbox_changed(state, combo, field_name):
                checked = state != 0
                self.geocoding.set_active(field_name, checked)
                combo.setVisible(checked)
            checkbox.stateChanged.connect(
                lambda s, c=combo, f=field_name : checkbox_changed(s, c, f))
            checkbox_changed(self.geocoding.active(field_name), combo,
                             field_name)

            def combo_changed(idx, combo, field_name):
                self.geocoding.set_keyword(field_name, combo.itemData(idx))
            combo.currentIndexChanged.connect(
                lambda i, c=combo, f=field_name : combo_changed(i, c, f))

            self.parameter_grid.addWidget(checkbox, i, 0)
            self.parameter_grid.addWidget(combo, i, 1)
            checked = self.geocoding.active(field_name)
            keyword = self.geocoding.keyword(field_name)
            checkbox.setChecked(checked)
            if keyword is not None:
                combo_idx = combo.findData(keyword)
                combo.setCurrentIndex(combo_idx)
                combo.setVisible(checked)
        n_selected = layer.selectedFeatureCount()
        #self.n_selected_label.setText(
            #'({} Feature(s) selektiert)'.format(n_selected))

    def geocode(self):
        layer = self.layer_combo.currentLayer()
        if not layer:
            return
        self.tab_widget.setCurrentIndex(2)
        active_count = self.geocoding.count_active()

        if active_count == 0:
            QMessageBox.information(
                self, 'Fehler',
                (u'Es sind keine Adressfelder ausgewählt.\n\n'
                 u'Start abgebrochen...'))
            return

        #for name, qtype, dbtype, length in BKG_FIELDS:
            #if name not in layer.fields().names():
                #layer.addAttribute(QgsField(name, qtype, dbtype, len=length))

        self.geocoding.run()