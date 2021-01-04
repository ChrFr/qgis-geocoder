# -*- coding: utf-8 -*-
'''
***************************************************************************
    main_widget.py
    ---------------------
    Date                 : December 2019
    Author               : Christoph Franke
    Copyright            : (C) 2020 by Bundesamt für Kartographie und Geodäsie
    Email                : franke at ggr-planung dot de
***************************************************************************
*                                                                         *
*   This program is free software: you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 3 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************

main dockable UI widget controlling the inputs, outputs and the calls of the
geocoding functions
loads further UI elements
'''

__author__ = 'Christoph Franke'
__date__ = '16/12/2019'
__copyright__ = 'Copyright 2020, Bundesamt für Kartographie und Geodäsie'

import os
import webbrowser
import re

from typing import List
from qgis.PyQt.QtCore import pyqtSignal, Qt, QTimer
from qgis import utils
from qgis.core import (QgsPointXY, QgsGeometry, QgsMapLayerProxyModel,
                       QgsVectorDataProvider, QgsWkbTypes, QgsVectorLayer,
                       QgsCoordinateTransform, QgsProject, QgsFeature, Qgis,
                       QgsPalLayerSettings, QgsTextFormat, QgsMessageLog,
                       QgsTextBufferSettings, QgsVectorLayerSimpleLabeling,
                       QgsCoordinateReferenceSystem)
from qgis.PyQt.QtWidgets import (QComboBox, QCheckBox, QMessageBox,
                                 QDockWidget, QFileDialog)
from qgis.PyQt.QtGui import QTextCursor

from .dialogs import ReverseResultsDialog, InspectResultsDialog, AboutDialog
from .map_tools import FeaturePicker, FeatureDragger
from .utils import (clone_layer, TopPlusOpen, get_geometries, LayerWrapper,
                    clear_layout, ResField)
from bkggeocoder.geocoder.bkg_geocoder import (BKGGeocoder, RS_PRESETS,
                                               BKG_RESULT_FIELDS)
from bkggeocoder.geocoder.geocoder import Geocoding, FieldMap, ReverseGeocoding
from bkggeocoder.config import Config, STYLE_PATH, HELP_URL, DEFAULT_STYLE
from bkggeocoder.interface.ui.main_dockwidget import Ui_MainDockWidget
import datetime

config = Config()


def field_comp(layer: QgsVectorLayer, field_name: str) -> str:
    '''return compatible field name depending on data provider (looking at
    you ESRI)'''
    provider_type = layer.dataProvider().storageType()
    if provider_type == 'ESRI Shapefile':
        field_name = field_name[:10]
    return field_name


class MainWidget(QDockWidget, Ui_MainDockWidget):
    '''
    the dockable main widget

    Attributes
    ----------
    closingWidget : pyqtSignal
        emitted when widget is closed in any way
    '''
    closingWidget = pyqtSignal()

    def __init__(self):
        super(MainWidget, self).__init__()
        # currently selected output layer
        self.output = None
        # stores which layers are marked as output layers
        self.output_layer_ids = []

        self.input = None
        self.label_field_name = None
        # cache all results for a layer, (layer-id, feature-id) as keys,
        # geojson features as values
        self.result_cache = {}
        # cache field-map settings for layers, layer-ids as keys,
        # FieldMaps as values
        self.field_map_cache = {}
        # cache label fields, layer-ids as keys, field name as values
        self.label_cache = {}
        self.field_map = None

        add_fields = [
            ResField('n_results', 'int2', alias='Anzahl der Ergebnisse',
                     prefix='gc'),
            ResField('i', 'int2', alias='Ergebnisindex', prefix='gc'),
            ResField('manuell_bearbeitet', 'bool', alias='Manuell bearbeitet')
        ]
        add_fields += BKG_RESULT_FIELDS

        # additional fields for storing results,
        # non-optional fields are active by default, others will be set by
        # user input
        self.result_fields = {f.name: (f, True if not f.optional else False)
                              for f in add_fields}

        self.inspect_dialog = None
        self.reverse_dialog = None
        self.geocoding = None

        self.iface = utils.iface
        self.canvas = self.iface.mapCanvas()
        self.setupUi()
        self.setup_config()

        # load background maps on start
        bg_grey = TopPlusOpen(groupname='Hintergrundkarten', greyscale=True,
                              crs='EPSG:25832')#config.projection)
        bg_grey.draw('TopPlusOpen Graustufen (bkg.bund.de)', checked=True)
        bg_osm = TopPlusOpen(groupname='Hintergrundkarten',
                             crs='EPSG:25832')#crs=config.projection)
        bg_osm.draw('TopPlusOpen (bkg.bund.de)', checked=False)
        for layer in [bg_osm, bg_grey]:
            layer.layer.setTitle(
                '© Bundesamt für Kartographie und Geodäsie 2020, '
                'Datenquellen: https://sg.geodatenzentrum.de/web_public/'
                'Datenquellen_TopPlus_Open.pdf')

    def setupUi(self):
        '''
        set up the ui, fill it with dynamic content and connect all interactive
        ui elements with actions
        '''
        super().setupUi(self)
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        # connect buttons
        self.import_csv_button.clicked.connect(self.import_csv)
        self.export_csv_button.clicked.connect(self.export_csv)
        self.attribute_table_button.clicked.connect(self.show_attribute_table)
        self.request_start_button.clicked.connect(self.bkg_geocode)
        self.request_stop_button.clicked.connect(lambda: self.geocoding.kill())
        self.request_stop_button.setVisible(False)

        # temporarily disabled help buttons
        self.help_button.setVisible(False)
        self.rsinfo_button.setVisible(False)
        #self.help_button.clicked.connect(self.show_help)
        #self.rsinfo_button.clicked.connect(
            #lambda: self.show_help(tag='regionalschluessel'))
        self.about_button.clicked.connect(self.show_about)

        # only vector layers as input
        self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layer_combo.layerChanged.connect(self.change_layer)

        # input layer encodings
        for encoding in QgsVectorDataProvider.availableEncodings():
            self.encoding_combo.addItem(encoding)
        self.encoding_combo.currentTextChanged.connect(self.set_encoding)

        # initially set the first layer in the combobox as input
        self.change_layer(self.layer_combo.currentLayer())

        # "Regionalschlüssel" filter
        self.rs_combo.addItem('Eingabehilfe Bundesländer')
        self.rs_combo.model().item(0).setEnabled(False)
        for name, rs in RS_PRESETS:
            self.rs_combo.addItem(name, rs)
        self.rs_combo.currentIndexChanged.connect(
            lambda: self.rs_edit.setText(self.rs_combo.currentData()))
        def rs_finished():
            self.rs_combo.blockSignals(True)
            self.rs_combo.setCurrentIndex(0)
            self.rs_combo.blockSignals(False)
        self.rs_edit.editingFinished.connect(rs_finished)
        def set_rs(rs):
            valid = self.check_rs(rs)
            self.rs_error_label.setVisible(
                not valid and self.use_rs_check.isChecked())
        self.rs_edit.textChanged.connect(set_rs)
        self.use_rs_check.toggled.connect(
            lambda: set_rs(self.rs_edit.text()))

        # spatial filter
        # only polygons can be used as a spatial filter
        self.spatial_filter_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)

        # connect map tools
        self.inspect_picker = FeaturePicker(
            self.inspect_picker_button, canvas=self.canvas)
        self.inspect_picker.feature_picked.connect(self.inspect_results)
        self.reverse_picker = FeatureDragger(
            self.reverse_picker_button, canvas=self.canvas)
        self.reverse_picker.feature_dragged.connect(self.inspect_neighbours)

        self.reverse_picker_button.setEnabled(False)
        self.inspect_picker_button.setEnabled(False)
        self.export_csv_button.setEnabled(False)
        self.attribute_table_button.setEnabled(False)

        # initialize the timer running when geocoding
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)
        self._dragged_feature = None

        self.setup_crs()

        # unregister layers from plugin when they are removed from QGIS
        # avoids errors when user is messing around in the QGIS UI
        QgsProject.instance().layersRemoved.connect(self.unregister_layers)

    def setup_config(self):
        '''
        apply all settings from the config file to the ui, connect ui elements
        of the config section to storing changes in this file
        '''
        # search options ('expert mode')
        self.search_and_check.setChecked(config.logic_link == 'AND')
        self.search_and_check.toggled.connect(
            lambda: setattr(config, 'logic_link', 'AND'))
        self.search_or_check.toggled.connect(
            lambda: setattr(config, 'logic_link', 'OR'))
        self.fuzzy_check.setChecked(config.fuzzy)
        self.fuzzy_check.toggled.connect(
            lambda checked: setattr(config, 'fuzzy', checked))

        # API key and url
        self.api_key_edit.setText(config.api_key)
        self.api_url_edit.setText(config.api_url)
        def api_key_edited():
            api_key = self.api_key_edit.text()
            setattr(config, 'api_key', api_key)
            url = BKGGeocoder.get_url(api_key)
            self.api_url_edit.setText(url)
            setattr(config, 'api_url', url)
            self.setup_crs()
        self.api_key_edit.editingFinished.connect(api_key_edited)
        self.api_url_edit.editingFinished.connect(
            lambda: setattr(config, 'api_url', self.api_url_edit.text()))
        self.api_url_edit.editingFinished.connect(self.setup_crs)
        self.reload_UUID_button.clicked.connect(self.setup_crs)

        if config.use_api_url:
            self.api_url_check.setChecked(True)
        else:
            self.api_key_check.setChecked(True)
        self.api_key_check.toggled.connect(
            lambda checked: setattr(config, 'use_api_url', not checked))

        # crs config
        idx = self.output_projection_combo.findData(config.projection)
        self.output_projection_combo.setCurrentIndex(idx)
        self.output_projection_combo.currentIndexChanged.connect(
            lambda: setattr(config, 'projection',
                            self.output_projection_combo.currentData()))

        # filters ("Regionalschlüssel" and spatial filter)
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
        self.debug_check.setChecked(config.debug)
        self.debug_check.toggled.connect(
            lambda checked: setattr(config, 'debug', checked))

        # output layer style
        # workaround: version change included name changes of predefined styles
        if not os.path.exists(config.output_style):
            config.output_style = DEFAULT_STYLE
        self.layer_style_edit.setText(config.output_style)
        self.layer_style_edit.editingFinished.connect(
            lambda path: setattr(config, 'output_style', path))
        self.layer_style_edit.editingFinished.connect(self.apply_output_style)

        def browse_file():
            path, sf = QFileDialog.getOpenFileName(
                self, 'Layerstil wählen', filter="QGIS-Layerstildatei(*.qml)",
                directory=STYLE_PATH)
            if path:
                self.layer_style_edit.setText(path)
                config.output_style = path
                self.apply_output_style()
        self.style_browse_button.clicked.connect(browse_file)

        # label field
        self.label_field_combo.currentIndexChanged.connect(self.apply_label)

        # additional result fields
        grid = self.output_fields_group.layout()
        i = 0
        def toggle_result_field(field, checked):
            self.result_fields[field.name] = field, checked
            result_fields = config.result_fields
            if checked:
                if field.name not in config.result_fields:
                    result_fields.append(field.name)
            elif field.name in config.result_fields:
                result_fields.remove(field.name)
            # set to trigger auto-write
            config.result_fields = result_fields

        # selectable optional result fields
        for field, active in self.result_fields.values():
            if field.optional:
                label = field.alias.replace(' laut Dienst', '')
                check = QCheckBox(label)
                checked = field.name in config.result_fields
                check.setChecked(checked)
                self.result_fields[field.name] = field, checked
                check.toggled.connect(
                    lambda state, f=field: toggle_result_field(f, state))
                grid.addWidget(check, i // 2, i % 2)
                i += 1

    def apply_output_style(self):
        '''
        apply currently set style file to current output layer
        '''
        layer = self.output.layer if self.output else None
        if not layer:
            return
        self.canvas.refresh()
        layer.loadNamedStyle(config.output_style)
        for field, active in self.result_fields.values():
            idx = field.idx(layer)
            layer.setFieldAlias(idx, field.alias)
        if self.label_field_name:
            self.apply_label()

    def apply_label(self):
        '''
        apply the label of the currently selected label field to the output
        layer
        '''
        layer = self.input.layer if self.input else None
        if not layer:
            return
        self.label_field_name = self.label_field_combo.currentData()
        self.label_cache[layer.id] = self.label_field_name

        layer = self.output.layer if self.output else None
        if not layer:
            return
        if not self.label_field_name:
            layer.setLabelsEnabled(False)
            layer.reload()
            return

        layer.setLabelsEnabled(True)
        labeling = layer.labeling()
        if not labeling:
            settings = QgsPalLayerSettings()
            settings.enabled = True
            buffer = QgsTextBufferSettings()
            buffer.setEnabled(True)
            buffer.setSize(0.8)
            text_format = QgsTextFormat()
            text_format.setBuffer(buffer)
            text_format.setSize(8)
            settings.setFormat(text_format)
            labeling = QgsVectorLayerSimpleLabeling(settings)
        settings = labeling.settings()
        settings.fieldName = self.label_field_name
        labeling.setSettings(settings)
        layer.setLabeling(labeling)
        layer.reload()

    def check_rs(self, rs: str) -> bool:
        '''
        validate the given "Regionalschlüssel"

        Parameters
        ----------
        rs: str
            "Regionalschlüssel" to validate

        Returns
        -------
        bool
            True if valid, False if not valid
        '''
        if not rs:
            return False
        regex = '^[01]\d{0,11}\*?$'
        return re.match(regex, rs) is not None

    def setup_crs(self):
        '''
        request service-url for available crs and populate crs-combobox with
        retrieved values
        '''
        self.uuid_group.setEnabled(False)
        self.request_start_button.setEnabled(False)
        current_crs = self.output_projection_combo.currentData()
        self.output_projection_combo.clear()
        # fill crs combo
        url = config.api_url if config.use_api_url else None
        success, msg, available_crs = BKGGeocoder.get_crs(key=config.api_key,
                                                          url=url)
        self.key_error_label.setText(msg)
        self.key_error_label.setVisible(not success)
        self.request_start_button.setEnabled(success)
        for code, name in available_crs:
            self.output_projection_combo.addItem(f'{name} ({code})', code)
        if current_crs:
            idx = self.output_projection_combo.findData(current_crs)
            self.output_projection_combo.setCurrentIndex(idx)
        self.uuid_group.setEnabled(True)

    def inspect_results(self, feature_id: int):
        '''
        open inspect dialog with results listed for feature with given id of
        current output-layer

        Parameters
        ----------
        feature_id : int
            id of the feature to inspect the results of
        '''
        layer = self.output.layer if self.output else None
        if not layer:
            return
        # get the results for given feature id from the cache
        results = self.result_cache.get((layer.id(), feature_id), None)
        # ToDo: warning dialog or pass it to results diag and show warning there
        if not results:
            return
        # close dialog if there is already one opened
        if self.inspect_dialog:
            self.inspect_dialog.close()
        feature = layer.getFeature(feature_id)
        # fields and their values that were active during search are shown
        # in dialog
        review_fields = [f for f in self.field_map.fields()
                         if self.field_map.active(f)]
        label = (feature.attribute(self.label_field_name)
                 if self.label_field_name else '')

        self.inspect_dialog = InspectResultsDialog(
            feature, results, self.canvas,
            preselect=feature.attribute(self.result_fields['i'][0].field_name),
            crs=layer.crs().authid(), review_fields=review_fields, label=label,
            text_field=self.result_fields['text'][0].field_name
        )
        accepted = self.inspect_dialog.exec_()
        # set picked result when user accepted
        if accepted:
            self.set_bkg_result(feature, self.inspect_dialog.result,
                                i=self.inspect_dialog.i, set_edited=True)
        self.canvas.refresh()
        self.inspect_dialog = None

    def inspect_neighbours(self, feature_id: int, point: QgsPointXY):
        '''
        reverse geocode given point, open dialog to pick result from and apply
        user choice to given dragged feature

        Parameters
        ----------
        feature_id : int
            id of the feature to set the reverse geocoding results to,
            was most likely dragged by user to new position
        point : QgsPointXY
            the position the feature was dragged to
        '''

        layer = self.output.layer if self.output else None
        if not layer:
            return
        layer.startEditing()

        dragged_feature = layer.getFeature(feature_id)
        prev_dragged_id = (self._dragged_feature.id()
                           if self._dragged_feature else None)
        if feature_id != prev_dragged_id:
            if self.reverse_dialog:
                self.reverse_dialog.close()
            # reset geometry of previously dragged feature
            if prev_dragged_id is not None:
                layer.changeGeometry(
                    prev_dragged_id, self._init_drag_geom)
            # remember initial geometry because geometry of dragged feature
            # will be changed in place
            self._init_drag_geom = dragged_feature.geometry()
            self._dragged_feature = dragged_feature

        crs = layer.crs().authid()
        url = config.api_url if config.use_api_url else None

        output_crs = layer.crs()
        # point geometry originates from clicking on canvas ->
        # transform into crs of feature
        transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(),
            output_crs,
            QgsProject.instance()
        )
        current_geom = QgsGeometry.fromPointXY(transform.transform(point))

        # apply the geometry to the feature
        layer.changeGeometry(feature_id, current_geom)

        # request feature again, otherwise geometry remains be unchanged
        dragged_feature = layer.getFeature(feature_id)
        bkg_geocoder = BKGGeocoder(key=config.api_key, crs=crs, url=url,
                                   logic_link=config.logic_link)
        rev_geocoding = ReverseGeocoding(bkg_geocoder, [dragged_feature],
                                         parent=self)
        def error(msg, level):
            self.log(msg, debug_only=True, level=level)
            QMessageBox.information(self, 'Fehler', msg)
        rev_geocoding.error.connect(lambda msg: error(msg, Qgis.Critical))
        rev_geocoding.warning.connect(lambda msg: error(msg, Qgis.Warning))
        rev_geocoding.message.connect(
            lambda msg: self.log(msg, debug_only=True))

        def done(feature, r):
            '''open dialog / set results when reverse geocoding is done'''
            results = r.json()['features']
            # only one opened dialog at a time
            if not self.reverse_dialog:
                review_fields = [f for f in self.field_map.fields()
                                 if self.field_map.active(f)]
                # remember the initial geometry
                self._init_rev_geom = feature.geometry()
                label = (feature.attribute(self.label_field_name)
                         if self.label_field_name else '')
                self.reverse_dialog = ReverseResultsDialog(
                    feature, results, self.canvas, review_fields=review_fields,
                    crs=output_crs.authid(), label=label,
                    text_field=self.result_fields['text'][0].field_name
                )
                accepted = self.reverse_dialog.exec_()
                if accepted:
                    result = self.reverse_dialog.result
                    # apply the geometry of the selected result
                    # (no result is selected -> geometry of dragged point is
                    # kept)
                    if result:
                        result['properties']['score'] = 1
                        self.set_bkg_result(
                            feature, result, i=-1, set_edited=True,
                            geom_only=self.reverse_dialog.geom_only
                            #,apply_adress=not self.reverse_dialog.geom_only
                        )
                    self.result_fields['manuell_bearbeitet'][0].set_value(
                        layer, feature_id, True)
                else:
                    # reset the geometry if rejected
                    try:
                        layer.changeGeometry(
                            self._dragged_feature.id(), self._init_drag_geom)
                    # catch error when quitting QGIS with dialog opened
                    # (layer is already deleted at this point)
                    except RuntimeError:
                        pass
                self._dragged_feature = None
                self.canvas.refresh()
                self.reverse_dialog = None
                self.reverse_picker.reset()
            else:
                # workaround for updating the feature position inside
                # the dialog
                self.reverse_dialog.feature.setGeometry(current_geom)
                # update the result options in the dialog
                self.reverse_dialog.update_results(results)

        # do the actual reverse geocoding
        rev_geocoding.feature_done.connect(done)
        rev_geocoding.start()

    def show_attribute_table(self):
        '''
        open the QGIS attribute table for current output layer
        '''
        layer = self.output.layer
        if not self.output.layer:
            return
        self.iface.showAttributeTable(layer)

    def unregister_layers(self, layer_ids: List[str]):
        '''
        removes all cached relations to given layers and resets output or
        input if they are part of the given layer list

        Parameters
        ----------
        layer_ids : list
             list of ids of layers to unregister
        '''
        io_removed = False
        for layer_id in layer_ids:
            self.field_map_cache.pop(layer_id, None)
            self.label_cache.pop(layer_id, None)
            # remove results if layer was output layer
            if layer_id in self.output_layer_ids:
                self.output_layer_ids.remove(layer_id)
                remove_keys = [k for k in self.result_cache.keys()
                               if k[0] == layer_id]
                for k in remove_keys:
                    self.result_cache.pop(k)
            # current output layer removed -> reset ui
            if self.output and layer_id == self.output.id:
                self.reset_output()
                io_removed = True
                self.log('Ergebnisse wurden zurückgesetzt, da der '
                         'Ergebnislayer entfernt wurde.', level=Qgis.Warning)
            if self.input and layer_id == self.input.id:
                self.input = None
                io_removed = True
        if io_removed and self.geocoding:
            self.geocoding.kill()
            self.log('Eingabe-/Ausgabelayer wurden während des '
                     'Geocodings gelöscht. Breche ab...', level=Qgis.Critical)

    def reset_output(self):
        '''
        resets the current output to none and disables UI elements connected
        to the results
        '''
        self.output = None
        self.reverse_picker_button.setEnabled(False)
        self.inspect_picker_button.setEnabled(False)
        self.export_csv_button.setEnabled(False)
        self.attribute_table_button.setEnabled(False)
        self.reverse_picker.set_active(False)
        self.inspect_picker.set_active(False)
        if self.inspect_dialog:
            self.inspect_dialog.close()
        if self.reverse_dialog:
            self.reverse_dialog.close()

    def export_csv(self):
        '''
        open the QGIS export dialog
        '''
        layer = self.output.layer if self.output else None
        if not layer:
            return
        self.iface.setActiveLayer(layer)
        actions = self.iface.layerMenu().actions()
        for action in actions:
            if action.objectName() == 'mActionLayerSaveAs':
                break
        action.trigger()

    def import_csv(self):
        '''
        open the QGIS import dialog with CSV preselected
        '''
        actions = self.iface.addLayerMenu().actions()
        for action in actions:
            if action.objectName() == 'mActionAddDelimitedText':
                break
        action.trigger()

    def unload(self):
        pass

    def closeEvent(self, event):
        '''
        override, emit closing signal
        '''
        self.reverse_picker.set_active(False)
        self.inspect_picker.set_active(False)
        self.iface.removeDockWidget(self)
        self.closingWidget.emit()
        event.accept()

    def show(self):
        '''
        open this widget
        '''
        # dock widget has to start docked
        self.iface.addDockWidget(Qt.LeftDockWidgetArea, self)

        # undock it immediately and resize to content
        self.setFloating(True)
        # switch to config tab to update min size of dock widget
        # otherwise widget tends to be have a very high height (as if all
        # config groups are expanded)
        self.tab_widget.setCurrentIndex(1)
        height = self.config_request_output_tab.layout().minimumSize().height()
        # set a fixed position, otherwise it is floating in a weird position
        self.setGeometry(500, 500, self.sizeHint().width(), height + 100)
        # switch back to input tab
        self.tab_widget.setCurrentIndex(0)
        # for some reason the height is ignored when setting geometry when
        # calling show() the first time
        self.resize(self.sizeHint().width(), height + 100)

    def log(self, text: str, level: int = Qgis.Info, debug_only=False):
        '''
        display given text in the log section

        Parameters
        ----------
        text : str
            the text to display in the log
        color : int, optional
            the qgis message level, defaults to Info
        '''
        color = 'black' if level == Qgis.Info else 'red' \
            if level == Qgis.Critical else 'orange'
        # don't show debug messages in log section
        if not debug_only:
            self.log_edit.moveCursor(QTextCursor.End)
            self.log_edit.insertHtml(
                f'<span style="color: {color}">{text}</span><br>')
            scrollbar = self.log_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        # always show critical messages in debug log, others only in debug mode
        if level == Qgis.Critical or config.debug:
            QgsMessageLog.logMessage(text, 'BKG Geocoder', level=level)

    def change_layer(self, layer: QgsVectorLayer):
        '''
        sets given layer to being the input of the geocoding,
        add field checks depending on given layer to UI and preset layer-related
        UI elements

        Parameters
        ----------
        layer : QgsVectorLayer
            the layer to change the UI to
        '''
        if not layer:
            return
        # set layer combo to given layer if it is not set to it
        if self.layer_combo.currentLayer().id() != layer.id():
            idx = -1
            for idx in range(len(self.layer_combo)):
                if self.layer_combo.layer(idx).id() == layer.id():
                    break
            self.layer_combo.setCurrentIndex(idx)

        self.input = LayerWrapper(layer)

        # layer can only be updated in place if it has a point geometry
        if layer.wkbType() != QgsWkbTypes.Point:
            self.update_input_layer_check.setChecked(False)
            self.update_input_layer_check.setEnabled(False)
        else:
            self.update_input_layer_check.setEnabled(True)
            # by default store results in selected layer if it is an output
            # layer. otherwise use create a new output layer when geocoding
            # (can be overridden by user)
            self.update_input_layer_check.setChecked(
                layer.id() in self.output_layer_ids)

        # set selected encoding in combobox to encoding of layer
        encoding = layer.dataProvider().encoding()
        self.encoding_combo.blockSignals(True)
        self.encoding_combo.setCurrentText(encoding)
        self.encoding_combo.blockSignals(False)

        # get field map with previous settings if layer was already used as
        # input before
        self.field_map = self.field_map_cache.get(layer.id(), None)
        if not self.field_map or not self.field_map.valid(layer):
            # if no field map was set yet, create it with the known BKG
            # keywords
            # ignore result fields (can't be mapped)
            bkg_f = [f[0].field_name_comp(layer)
                     for f in self.result_fields.values()]
            self.field_map = FieldMap(layer, ignore=bkg_f,
                                      keywords=BKGGeocoder.keywords)
            self.field_map_cache[layer.id()] = self.field_map
        # remove old widgets
        clear_layout(self.parameter_grid)

        # create a list of checkable items out of the fields of the layer
        for i, field_name in enumerate(self.field_map.fields()):
            checkbox = QCheckBox()
            checkbox.setText(field_name)
            # combobox for user-selection of a API-keyword matching the field
            combo = QComboBox()
            combo.addItem('Volltextsuche', None)
            for key, text in BKGGeocoder.keywords.items():
                combo.addItem(text, key)

            def checkbox_changed(state: bool, combo: QComboBox,
                                 field_name: str):
                checked = state != 0
                self.field_map.set_active(field_name, checked)
                combo.setEnabled(checked)
            # apply changes to field map and combobox on check-state change
            checkbox.stateChanged.connect(
                lambda s, c=combo, f=field_name : checkbox_changed(s, c, f))
            # set initial check state
            checkbox_changed(self.field_map.active(field_name), combo,
                             field_name)

            def combo_changed(idx: int, combo: QComboBox, field_name: str):
                self.field_map.set_keyword(field_name, combo.itemData(idx))
            # apply changes field map when selecting different keyword
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

        # label selection
        self.label_field_combo.blockSignals(True)
        self.label_field_combo.clear()
        self.label_field_combo.addItem('kein Label')
        aliases = {f[0].field_name_comp(layer): f[0].alias
                   for f in self.result_fields.values()}
        for field in layer.fields():
            field_name = field.name()
            alias = aliases.get(field_name)
            self.label_field_combo.addItem(alias or field_name, field_name)
        self.label_field_combo.blockSignals(False)

        # try to set prev. selected field
        label_field = self.label_cache.get(layer.id())
        if label_field is None and self.output and self.output.layer:
            label_field = self.label_cache.get(self.output.id)
        idx = self.label_field_combo.findData(label_field)
        self.label_field_combo.setCurrentIndex(max(idx, 0))

    def set_encoding(self, encoding: str):
        '''
        set encoding of input layer and redraw the parameter section

        Parameters
        ----------
        encoding : str
            the name of the encoding e.g. 'utf-8'
        '''
        layer = self.input.layer if self.input else None
        if not layer:
            return
        layer.dataProvider().setEncoding(encoding)
        layer.updateFields()
        # repopulate fields
        self.change_layer(layer)

    def bkg_geocode(self):
        '''
        start geocoding of input layer with current settings
        '''
        layer = self.input.layer if self.input else None
        if not layer:
            return
        self.progress_bar.setStyleSheet('')
        self.reverse_picker_button.setEnabled(False)
        self.inspect_picker_button.setEnabled(False)
        self.export_csv_button.setEnabled(False)
        self.attribute_table_button.setEnabled(False)

        active_count = self.field_map.count_active()
        if active_count == 0:
            QMessageBox.information(
                self, 'Fehler',
                (u'Es sind keine Adressfelder ausgewählt.\n\n'
                 u'Start abgebrochen...'))
            return

        rs = None
        if self.use_rs_check.isChecked():
            valid = self.check_rs(config.rs)
            if not valid:
                self.log('Der Regionalschlüssel ist ungültig und wird '
                         'ignoriert.', level=Qgis.Warning)
            else:
                rs = config.rs

        features = layer.selectedFeatures() \
            if config.selected_features_only else layer.getFeatures()

        # input layer is flagged as output layer
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
            self.output = LayerWrapper(layer)
            self.output.layer.setCrs(
                QgsCoordinateReferenceSystem(config.projection))
        # create output layer as a clone of input layer
        else:
            self.output = LayerWrapper(clone_layer(
                layer, name=f'{layer.name()}_ergebnisse',
                crs=config.projection, features=features))
            QgsProject.instance().addMapLayer(self.output.layer, False)
            # add output to same group as input layer
            tree_layer = QgsProject.instance().layerTreeRoot().findLayer(layer)
            group = tree_layer.parent()
            group.insertLayer(0, self.output.layer)
            self.output_layer_ids.append(self.output.id)
            # cloned layer gets same mapping, it has the same fields
            cloned_field_map = self.field_map.copy(layer=self.output.layer)
            self.field_map_cache[self.output.id] = cloned_field_map
            self.label_cache[self.output.id] =\
                self.label_cache.get(layer.id())
            # take features of output layer as input to match the ids of the
            # geocoding
            features = [f for f in self.output.layer.getFeatures()]

        self.apply_label()

        layer.setReadOnly(True)
        self.output.layer.setReadOnly(True)

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

        url = config.api_url if config.use_api_url else None

        bkg_geocoder = BKGGeocoder(key=config.api_key, crs=config.projection,
                                   url=url, logic_link=config.logic_link, rs=rs,
                                   area_wkt=area_wkt, fuzzy=config.fuzzy)
        self.geocoding = Geocoding(bkg_geocoder, self.field_map,
                                   features=features, parent=self)

        self.geocoding.message.connect(
            lambda msg: self.log(msg, debug_only=True))

        def feature_done(f, r):
            label = f.attribute(self.label_field_name) \
                if (self.label_field_name) else f'Feature {f.id()}'
            results = r.json()['features']
            message = (f'{label} -> <b>{len(results)} </b> Ergebnis(se)')
            self.log(message, level=Qgis.Info)
            self.output.layer.setReadOnly(False)
            self.store_bkg_results(f, results)
            self.output.layer.setReadOnly(True)

        self.geocoding.progress.connect(self.progress_bar.setValue)
        self.geocoding.feature_done.connect(feature_done)
        self.geocoding.error.connect(
            lambda msg: self.log(msg, level=Qgis.Critical))
        self.geocoding.warning.connect(
            lambda msg: self.log(msg, level=Qgis.Warning))
        self.geocoding.finished.connect(self.geocoding_done)

        self.inspect_picker.set_layer(self.output.layer)
        self.reverse_picker.set_layer(self.output.layer)

        self.tab_widget.setCurrentIndex(2)

        # add active result fields if they are not already part of the layer
        add_fields = [f[0].to_qgs_field() for f in self.result_fields.values()
                      if f[1] and f[0].idx(layer) < 0]
        self.output.layer.dataProvider().addAttributes(add_fields)
        self.output.layer.updateFields()
        self.apply_output_style()

        self.request_start_button.setVisible(False)
        self.request_stop_button.setVisible(True)
        self.log(f'<br>Starte Geokodierung <b>{layer.name()}</b>')
        self.start_time = datetime.datetime.now()
        self.timer.start(1000)
        self.geocoding.start()

    def update_timer(self):
        '''
        update the timer counting the time since starting the geocoding
        '''
        delta = datetime.datetime.now() - self.start_time
        h, remainder = divmod(delta.seconds, 3600)
        m, s = divmod(remainder, 60)
        timer_text = '{:02d}:{:02d}:{:02d}'.format(h, m, s)
        self.elapsed_time_label.setText(timer_text)

    def geocoding_done(self, success: bool):
        '''
        update UI when geocoding is done

        Parameters
        ----------
        success : bool
            whether the geocoding was run successfully without errors or not
        '''
        self.geocoding = None
        if not self.input or not self.output:
            return
        self.input.layer.setReadOnly(False)
        self.output.layer.setReadOnly(False)
        if success:
            self.log('Geokodierung erfolgreich abgeschlossen')
        else:
            self.progress_bar.setStyleSheet(
                'QProgressBar::chunk {background-color: red;}')
        # select output layer as current layer
        self.layer_combo.setLayer(self.output.layer)
        # zoom to extent of results
        extent = self.output.layer.extent()
        if not extent.isEmpty():
            transform = QgsCoordinateTransform(
                self.output.layer.crs(),
                self.canvas.mapSettings().destinationCrs(),
                QgsProject.instance()
            )
            self.canvas.setExtent(transform.transform(extent))
            self.canvas.zoomByFactor(1.2)
        self.canvas.refresh()
        self.output.layer.reload()
        self.timer.stop()

        # update the states of the buttons
        self.request_start_button.setVisible(True)
        self.request_stop_button.setVisible(False)
        self.reverse_picker_button.setEnabled(True)
        self.inspect_picker_button.setEnabled(True)
        self.export_csv_button.setEnabled(True)
        self.attribute_table_button.setEnabled(True)

    def store_bkg_results(self, feature: QgsFeature, results: List[dict]):
        '''
        store the results (geojson features) per feature in the result cache

        Parameters
        ----------
        feature : QgsFeature
            the feature to store the results for
        results : list
            the geojson feature list of all matches returned by the BKG geocoder
        '''
        if not self.output:
            return
        if results:
            results.sort(key=lambda x: x['properties']['score'], reverse=True)
            best = results[0]
        else:
            best = None
        self.result_cache[self.output.id, feature.id()] = results
        self.set_bkg_result(feature, best, i=0, n_results=len(results))

    def set_bkg_result(self, feature: QgsFeature, result: dict, i: int = -1,
                       n_results: int = None, geom_only: bool = False,
                       set_edited: bool = False):  #, apply_adress=False):
        '''
        set result of BKG geocoding to given feature of current output layer (
        including properties 'typ', 'text', 'score', 'treffer' and the geometry)

        Parameters
        ----------
        feature : QgsFeature
            the feature to set the result to
        result : dict
            the geojson response of the BKG geocoder whose attributes to apply
            to the feature
        i : int, optional
            the index of the result in the list, -1 to indicate it is not in the
            list, defaults to not in results list
        n_results : int, optional
            number of results returned by the BKG geocoder in total,
            defaults to None
        geom_only : bool, optional
            only apply the geometry of the result to the feature, defaults to
            applying all atributes
        set_edited : bool, optional
            mark feature as manually edited, defaults to mark as not edited
        '''
        layer = self.output.layer if self.output else None
        if not layer:
            return
        if not layer.isEditable():
            layer.startEditing()

        feat_id = feature.id()
        if result:
            coords = result['geometry']['coordinates']
            geom = QgsGeometry.fromPointXY(QgsPointXY(coords[0], coords[1]))
            properties = result['properties']
            layer.changeGeometry(feat_id, geom)
            if not geom_only:
                # apply all result properties if corresponding field is
                # available and active
                for rf, active in self.result_fields.values():
                    if not active:
                        continue
                    value = properties.get(rf.name, None)
                    if value is not None:
                        rf.set_value(layer, feat_id, value)
                if n_results:
                    self.result_fields['n_results'][0].set_value(
                        layer, feat_id, value)
            self.result_fields['i'][0].set_value(layer, feat_id, i)
        else:
            for rf, active in self.result_fields.values():
                if active:
                    rf.set_value(layer, feat_id, None)
            layer.changeGeometry(feat_id, QgsGeometry())
        self.result_fields['manuell_bearbeitet'][0].set_value(
            layer, feat_id, set_edited)
        layer.commitChanges()

    def show_help(self, tag: str = ''):
        '''
        open help website in browser

        Parameters
        ----------
        tag : str
            anchor of help page to jump to on opening the help website,
            defaults to open first page
        '''
        url = HELP_URL
        if tag:
            url += f'#{tag}'
        webbrowser.open(url, new=0)

    def show_about(self):
        '''
        show information about plugin in dialog
        '''
        about = AboutDialog()
        about.exec_()