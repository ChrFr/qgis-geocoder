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

from qgis.PyQt import uic
from qgis.PyQt.QtCore import pyqtSignal, Qt, QVariant, QTimer
from qgis import utils
from qgis.core import (QgsCoordinateReferenceSystem, QgsField,
                       QgsPointXY, QgsGeometry, QgsMapLayerProxyModel,
                       QgsVectorDataProvider, QgsWkbTypes)
from qgis.PyQt.QtWidgets import (QComboBox, QCheckBox, QMessageBox,
                                 QDockWidget)

from interface.dialogs import ReverseResultsDialog, InspectResultsDialog
from interface.map_tools import FeaturePicker
from interface.utils import (clone_layer, TerrestrisBackgroundLayer,
                             OSMBackgroundLayer, get_geometries)
from geocoder.bkg_geocoder import BKGGeocoder
from geocoder.geocoder import Geocoding, FieldMap, ReverseGeocoding
from config import Config, STYLE_PATH, UI_PATH
import datetime

config = Config()

BKG_FIELDS = [
    ('bkg_n_results', QVariant.Int, 'int2'),
    ('bkg_i', QVariant.Double, 'int2'),
    ('bkg_typ', QVariant.String, 'text'),
    ('bkg_text', QVariant.String, 'text'),
    ('bkg_score', QVariant.Double, 'float8'),
    ('bkg_treffer', QVariant.String, 'text'),
    ('manuell_bearbeitet', QVariant.Bool, 'bool')
]

RS_PRESETS = [
    ('Schleswig-Holstein', '01*'),
    ('Freie und Hansestadt Hamburg', '02*'),
    ('Niedersachsen', '03*'),
    ('Freie Hansestadt Bremen', '04*'),
    ('Nordrhein-Westfalen', '05*'),
    ('Hessen', '06*'),
    ('Rheinland-Pfalz', '07*'),
    ('Baden-Württemberg', '08*'),
    ('Freistaat Bayern', '09*'),
    ('Saarland', '10*'),
    ('Berlin', '11*'),
    ('Brandenburg', '12*'),
    ('Mecklenburg-Vorpommern', '13*'),
    ('Freistaat Sachsen', '14*'),
    ('Sachsen-Anhalt', '15*'),
    ('Freistaat Thüringen', '16*')
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


class MainWidget(QDockWidget):
    ui_file = 'main_dockwidget.ui'
    closingWidget = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(MainWidget, self).__init__(parent)

        self.output_layer = None
        self.output_layer_ids = []
        self.input_layer = None
        self.result_cache = {}
        self.field_map_cache = {}
        self.inspect_dialog = None
        self.reverse_dialog = None

        self.iface = utils.iface
        self.canvas = self.iface.mapCanvas()
        ui_file = self.ui_file if os.path.exists(self.ui_file) \
            else os.path.join(UI_PATH, self.ui_file)
        uic.loadUi(ui_file, self)
        self.setAllowedAreas(
            Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea
        )
        self.setupUi()
        self.setup_config()

        bg_grey = TerrestrisBackgroundLayer(groupname='Hintergrundkarten',
                                            srs=config.projection)
        bg_grey.draw(checked=False)
        bg_osm = OSMBackgroundLayer(groupname='Hintergrundkarten',
                                    srs=config.projection)
        bg_osm.draw(checked=True)

    def setupUi(self):
        actions = self.iface.addLayerMenu().actions()
        for action in actions:
            if action.objectName() == 'mActionAddDelimitedText':
                self.import_csv_action = action
                break
        self.import_csv_button.clicked.connect(self.import_csv_action.trigger)
        self.export_csv_button.clicked.connect(self.export_csv)
        self.attribute_table_button.clicked.connect(self.show_attribute_table)
        self.request_start_button.clicked.connect(self.bkg_geocode)
        self.request_stop_button.clicked.connect(lambda: self.geocoding.kill())
        self.request_stop_button.setVisible(False)
        self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.spatial_filter_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.layer_combo.layerChanged.connect(self.change_layer)

        for encoding in QgsVectorDataProvider.availableEncodings():
            self.encoding_combo.addItem(encoding)
        self.encoding_combo.currentTextChanged.connect(self.set_encoding)

        self.change_layer(self.layer_combo.currentLayer())

        self.rs_combo.addItem('Eingabehilfe Bundesländer')
        self.rs_combo.model().item(0).setEnabled(False)
        for name, rs in RS_PRESETS:
            self.rs_combo.addItem(name, rs)
        self.rs_combo.currentIndexChanged.connect(
            lambda: self.rs_edit.setText(self.rs_combo.currentData()))
        self.rs_edit.editingFinished.connect(
            lambda: self.rs_combo.setCurrentIndex(0))

        self.inspect_picker = FeaturePicker(
            self.inspect_picker_button, canvas=self.canvas)
        self.inspect_picker.feature_picked.connect(self.inspect_results)
        self.reverse_picker = FeaturePicker(
            self.reverse_picker_button, canvas=self.canvas)
        self.reverse_picker.feature_picked.connect(self.reverse_geocode)

        self.reverse_picker_button.setEnabled(False)
        self.inspect_picker_button.setEnabled(False)
        self.export_csv_button.setEnabled(False)
        self.attribute_table_button.setEnabled(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)

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
            lambda checked: setattr(config, 'selected_features_only', checked))

        self.rs_edit.setText(config.rs)
        self.rs_edit.textChanged.connect(
            lambda text: setattr(config, 'rs', text))

        self.use_rs_check.setChecked(config.use_rs)
        self.use_rs_check.toggled.connect(
            lambda checked: setattr(config, 'use_rs', checked))

    def inspect_results(self, feature):
        if not self.output_layer:
            return
        results = self.result_cache.get((self.output_layer.id(), feature.id()),
                                        None)
        # ToDo: warning dialog or pass it to results diag and show warning there
        if not results or not self.output_layer:
            return
        self.output_layer.removeSelection()
        self.output_layer.select(feature.id())
        # close dialog if there is already one opened
        if self.inspect_dialog:
            self.inspect_dialog.close()
        review_fields = [f for f in self.field_map.fields()
                         if self.field_map.active(f)]
        self.inspect_dialog = InspectResultsDialog(
            feature, results, self.canvas, preselect=feature.attribute('bkg_i'),
            review_fields=review_fields, parent=self)
        accepted = self.inspect_dialog.show()
        if accepted:
            self.set_result(feature, self.inspect_dialog.result,
                            i=self.inspect_dialog.i, set_edited=True)
        # when you close QGIS with the dialog opened, the actual layer is
        # is already removed at this point
        try:
            self.output_layer.removeSelection()
        except:
            pass
        self.inspect_dialog = None

    def reverse_geocode(self, feature):
        if not self.output_layer:
            return
        self.output_layer.removeSelection()
        self.output_layer.select(feature.id())
        crs = self.output_layer.crs().authid()
        bkg_geocoder = BKGGeocoder(config.api_key, srs=crs,
                                   logic_link=config.logic_link)
        rev_geocoding = ReverseGeocoding(bkg_geocoder, [feature], parent=self)
        review_fields = [f for f in self.field_map.fields()
                         if self.field_map.active(f)]

        rev_geocoding.error.connect(
            lambda msg: QMessageBox.information(self, 'Fehler', msg))

        def done(feature, results):
            # only one opened dialog at a time
            if self.reverse_dialog:
                self.reverse_dialog.close()
            # the first result will always be closest
            # (meaning it is at exact position of requested feature)
            preselect = 0
            self.reverse_dialog = ReverseResultsDialog(
                feature, results, self.canvas, preselect=preselect,
                review_fields=review_fields, parent=self)
            accepted = self.reverse_dialog.show()
            if accepted:
                result = self.reverse_dialog.result
                if result:
                    self.set_result(
                        feature, result, i=-1, set_edited=True,
                        geom_only=self.reverse_dialog.geom_only,
                        apply_adress=not self.reverse_dialog.geom_only
                    )
            try:
                self.output_layer.removeSelection()
            except:
                pass
            self.reverse_dialog = None
        rev_geocoding.feature_done.connect(done)
        # ToDo: for some reason QGIS crashes while threading (with start())
        rev_geocoding.work()

    def show_attribute_table(self):
        if not self.output_layer:
            return
        self.iface.showAttributeTable(self.output_layer)

    def export_csv(self):
        if not self.output_layer:
            return
        self.iface.setActiveLayer(self.output_layer)
        actions = self.iface.layerMenu().actions()
        for action in actions:
            if action.objectName() == 'mActionLayerSaveAs':
                break
        action.trigger()

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

    def change_layer(self, layer, force_mapping=False):
        '''
        add field checks depending on given layer to UI and preset
        layer related UI elements
        '''
        if not layer:
            return

        self.input_layer = layer

        # by default store results in selected layer if it is an output layer
        # otherwise use create a new output layer when geocoding (can be over-
        # ridden by user)
        self.update_input_layer_check.setChecked(
            layer.id() in self.output_layer_ids)

        encoding = layer.dataProvider().encoding()
        self.encoding_combo.blockSignals(True)
        self.encoding_combo.setCurrentText(encoding)
        self.encoding_combo.blockSignals(False)

        bkg_f = [f[0] for f in BKG_FIELDS]
        self.field_map = self.field_map_cache.get(layer.id(), None)
        if not self.field_map or not self.field_map.valid(layer):
            self.field_map = FieldMap(layer, ignore=bkg_f,
                                      keywords=BKGGeocoder.keywords)
            self.field_map_cache[layer.id()] = self.field_map
        # remove old widgets
        clear_layout(self.parameter_grid)

        for i, field_name in enumerate(self.field_map.fields()):
            checkbox = QCheckBox()
            checkbox.setText(field_name)
            combo = QComboBox()
            combo.addItem('Volltextsuche', None)
            for key, text in BKGGeocoder.keywords.items():
                combo.addItem(text, key)

            def checkbox_changed(state, combo, field_name):
                checked = state != 0
                self.field_map.set_active(field_name, checked)
                combo.setEnabled(checked)
            checkbox.stateChanged.connect(
                lambda s, c=combo, f=field_name : checkbox_changed(s, c, f))
            # set initial check state
            checkbox_changed(self.field_map.active(field_name), combo,
                             field_name)

            def combo_changed(idx, combo, field_name):
                self.field_map.set_keyword(field_name, combo.itemData(idx))
            combo.currentIndexChanged.connect(
                lambda i, c=combo, f=field_name : combo_changed(i, c, f))
            # set initial combo index
            cur_idx = combo.findData(self.field_map.keyword(field_name))
            combo_changed(cur_idx, combo, field_name)

            self.parameter_grid.addWidget(checkbox, i, 0)
            self.parameter_grid.addWidget(combo, i, 1)
            # initial state
            checked = self.field_map.active(field_name)
            keyword = self.field_map.keyword(field_name)
            checkbox.setChecked(checked)
            if keyword is not None:
                combo_idx = combo.findData(keyword)
                combo.setCurrentIndex(combo_idx)
                combo.setEnabled(checked)

        #n_selected = layer.selectedFeatureCount()
        #self.n_selected_label.setText(
            #'({} Feature(s) selektiert)'.format(n_selected))

    def set_encoding(self, encoding):
        self.input_layer.dataProvider().setEncoding(encoding)
        self.input_layer.updateFields()
        # repopulate fields
        self.change_layer(self.input_layer, force_mapping=True)

    def bkg_geocode(self):
        layer = self.input_layer
        if not layer:
            return

        active_count = self.field_map.count_active()
        if active_count == 0:
            QMessageBox.information(
                self, 'Fehler',
                (u'Es sind keine Adressfelder ausgewählt.\n\n'
                 u'Start abgebrochen...'))
            return

        rs = config.rs if self.use_rs_check.isChecked() else None
        features = layer.selectedFeatures() if config.selected_features_only \
            else layer.getFeatures()
        # we use it 2 times, this avoids using same FeatureIterator twice
        features = [f for f in features]
        area_wkt = None
        if self.use_spatial_filter_check.isChecked():
            spatial_layer = self.spatial_filter_combo.currentLayer()
            if spatial_layer:
                selected_only = self.spatial_selected_only_check.isChecked()
                geometries = get_geometries(
                    spatial_layer, selected=selected_only,
                    crs=config.projection)
                union = None
                for geom in geometries:
                    union = geom if not union else union.combine(geom)
                area_wkt = union.asWkt()

        bkg_geocoder = BKGGeocoder(config.api_key, srs=config.projection,
                                   logic_link=config.logic_link, rs=rs,
                                   area_wkt=area_wkt)
        self.geocoding = Geocoding(bkg_geocoder, self.field_map,
                                   features=features, parent=self)

        if self.update_input_layer_check.isChecked():
            if layer.wkbType() != QgsWkbTypes.Point:
                QMessageBox.information(
                    self, 'Fehler',
                    (u'Der Layer enthält keine Punktgeometrie. Daher können '
                     u'die Ergebnisse nicht direkt dem Layer hinzugefügt '
                     u'werden.\n'
                     u'Fügen Sie dem Layer eine Punktgeometrie hinzu oder '
                     u'deaktivieren Sie die Checkbox '
                     u'"Ausgangslayer aktualisieren".\n\n'
                     u'Start abgebrochen...'))
                return
            self.output_layer = layer
        else:
            self.output_layer = clone_layer(
                layer, name=f'{layer.name()}_ergebnisse',
                srs=config.projection, features=features)
            self.output_layer_ids.append(self.output_layer.id())
            # cloned layer gets same mapping, it has the same fields
            cloned_field_map = self.field_map.copy(layer=self.output_layer)
            self.field_map_cache[self.output_layer.id()] = cloned_field_map

        style_file = os.path.join(STYLE_PATH, 'bkggeocoder_treffer.qml')
        self.output_layer.loadNamedStyle(style_file)

        self.geocoding.message.connect(self.log)
        self.geocoding.progress.connect(self.progress_bar.setValue)
        self.geocoding.feature_done.connect(self.store_results)
        self.geocoding.error.connect(lambda msg: self.log(msg, color='red'))
        self.geocoding.finished.connect(self.geocoding_done)

        self.inspect_picker.set_layer(self.output_layer)
        self.reverse_picker.set_layer(self.output_layer)

        self.tab_widget.setCurrentIndex(2)

        field_names = self.output_layer.fields().names()
        add_fields = [QgsField(n, q, d) for n, q, d in BKG_FIELDS
                      if n not in field_names]
        self.output_layer.dataProvider().addAttributes(add_fields)
        self.output_layer.updateFields()

        self.request_start_button.setVisible(False)
        self.request_stop_button.setVisible(True)
        self.log(f'<br>Starte Geokodierung <b>{layer.name()}</b>')
        self.start_time = datetime.datetime.now()
        self.timer.start(1000)
        self.geocoding.start()

    def update_timer(self):
        delta = datetime.datetime.now() - self.start_time
        h, remainder = divmod(delta.seconds, 3600)
        m, s = divmod(remainder, 60)
        timer_text = '{:02d}:{:02d}:{:02d}'.format(h, m, s)
        self.elapsed_time_label.setText(timer_text)

    def geocoding_done(self, success: bool):
        if success:
            self.log('Geokodierung erfolgreich abgeschlossen')
            # select output layer as current layer
            self.layer_combo.setLayer(self.output_layer)

        extent = self.output_layer.extent()
        if not extent.isEmpty():
            self.canvas.setExtent(extent)
        self.canvas.refresh()
        self.timer.stop()
        self.request_start_button.setVisible(True)
        self.request_stop_button.setVisible(False)
        self.reverse_picker_button.setEnabled(True)
        self.inspect_picker_button.setEnabled(True)
        self.export_csv_button.setEnabled(True)
        self.attribute_table_button.setEnabled(True)


    def store_results(self, feature, results):
        if results:
            results.sort(key=lambda x: x['properties']['score'])
            best = results[0]
        else:
            best = None
        self.result_cache[self.output_layer.id(), feature.id()] = results
        self.set_result(feature, best, i=0, n_results=len(results))

    def set_result(self, feature, result, i=0, n_results=None, geom_only=False,
                   apply_adress=False, set_edited=False):
        '''
        bkg specific
        set result to feature of given layer
        focus map canvas on feature if requested
        '''
        layer = self.output_layer
        if not layer.isEditable():
            layer.startEditing()
        fidx = layer.fields().indexFromName
        feat_id = feature.id()
        if result:
            coords = result['geometry']['coordinates']
            geom = QgsGeometry.fromPointXY(QgsPointXY(coords[0], coords[1]))
            properties = result['properties']
            layer.changeGeometry(feat_id, geom)
            if not geom_only:
                for prop in ['typ', 'text', 'score', 'treffer']:
                    value = properties.get(prop, None)
                    # property gets prefix bkg_ in layer
                    layer.changeAttributeValue(
                        feat_id, fidx(f'bkg_{prop}'), value)
                if apply_adress:
                    for field in self.field_map.fields():
                        if not self.field_map.active(field):
                            continue
                        value = properties.get(
                            self.field_map.keyword(field), None)
                        layer.changeAttributeValue(
                            feat_id, fidx(field), value)
                if n_results:
                    layer.changeAttributeValue(
                        feat_id, fidx('bkg_n_results'), n_results)
            layer.changeAttributeValue(feat_id, fidx('bkg_i'), i)
        else:
            layer.changeAttributeValue(
                feat_id, fidx('bkg_typ'), '')
            layer.changeAttributeValue(
                feat_id, fidx('bkg_score'), 0)
        layer.changeAttributeValue(
            feat_id, fidx('manuell_bearbeitet'), set_edited)
