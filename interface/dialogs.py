# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (QDialog, QLabel, QRadioButton, QGridLayout,
                                 QFrame)
from qgis.PyQt.Qt import QWidget
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt import uic
from qgis.core import (QgsPointXY, QgsGeometry, QgsVectorLayer, QgsFeature,
                       QgsField, QgsProject,
                       QgsCategorizedSymbolRenderer, QgsRendererCategory,
                       QgsMarkerSymbol, QgsRasterMarkerSymbolLayer,
                       QgsRectangle, QgsCoordinateTransform)
from qgis.gui import QgsMapCanvas
import os

from typing import List
from interface.utils import clear_layout
from config import UI_PATH, Config, ICON_PATH

config = Config()


class Dialog(QDialog):
    '''
    Dialog
    '''
    def __init__(self, ui_file: str = None, modal: bool = True,
                 parent: QWidget = None, title: str = None):
        '''
        Parameters
        ----------
        ui_file : str, optional
            path to QT-Designer xml file to load UI of dialog from,
            if only filename is given, the file is looked for in the standard
            folder (UI_PATH), defaults to None
        modal : bool, optional
            set dialog to modal, defaults to True
        parent: QWidget, optional
            parent widget, defaults to None
        title: str, optional
            replaces title of dialog if given, defaults to preset title
        '''

        super().__init__(parent=parent)
        if title:
            self.setWindowTitle(title)

        if ui_file:
            # look for file ui folder if not found
            ui_file = ui_file if os.path.exists(ui_file) \
                else os.path.join(UI_PATH, ui_file)
            uic.loadUi(ui_file, self)
        self.setModal(modal)
        self.setupUi()

    def setupUi(self):
        '''
        override for additional functionality
        '''
        pass

    def show(self):
        '''
        override, show the dialog
        '''
        return self.exec_()


class InspectResultsDialog(Dialog):
    '''
    dialog showing a feature with its attributes used for geocoding  and
    a list of pickable results of geocoding this feature
    '''
    ui_file = 'featurepicker.ui'
    marker_img = 'marker_{}.png'

    def __init__(self, feature: QgsFeature, results: List[dict],
                 canvas: QgsMapCanvas, review_fields: List[str] = [],
                 preselect: int = -1, crs: str = 'EPSG:4326',
                 parent: QWidget = None, show_score: bool = True):
        '''
        Parameters
        ----------
        feature : QWidget
            the feature to show alternative results for
        results : list
            alternative results to given feature to let user pick from,
            list of geojson features with "geometry" attribute and "properties"
            containing "text" (description of feature) and "score" (the higher
            the better ranking)
        canvas : QgsMapCanvas
            the map canvas to preview the results on
        review_fields : list, optional
            list of field names of the given feature whose values are shown in
            the dialog review section, defaults to empty list (no fields shown)
        preselect : int, optional
            preselects a result on showing the dialog, defaults to -1 (no result
            preselected)
        crs : str, optional
            code of projection of the geometries of the given features (feature
            and results), defaults to epsg 4326
        parent : QWidget, optional
            parent widget, defaults to None
        show_score : bool, optional
            show the score of the results in the ui, defaults to True
        '''
        super().__init__(self.ui_file, modal=False, parent=parent)
        self.canvas = canvas
        self.results = results
        self.feature = feature
        self.geom_only_button.setVisible(False)
        self.result = None
        self.i = -1
        self.show_score = True
        self.crs = crs

        self._populate_review(review_fields)
        self._setup_preview_layer()
        self._add_results(preselect=preselect)

        self.accept_button.clicked.connect(self.accept)
        self.discard_button.clicked.connect(self.reject)

    def _populate_review(self, review_fields : List[str]):
        '''
        populate the review section of the inspected feature with given
        fields and their current values
        '''
        if review_fields:
            headline = QLabel('Geokodierungs-Parameter')
            font = headline.font()
            font.setUnderline(True)
            headline.setFont(font)
            self.review_layout.addWidget(headline)
            grid = QGridLayout()
            for i, field in enumerate(review_fields):
                grid.addWidget(QLabel(field), i, 0)
                value = self.feature.attribute(field)
                grid.addWidget(QLabel(str(value)), i, 1)
            self.review_layout.addLayout(grid)
            # horizontal line
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Sunken)
            self.review_layout.addWidget(line)

        headline = QLabel('Anschrift laut Dienst')
        font = headline.font()
        font.setUnderline(True)
        headline.setFont(font)
        self.review_layout.addWidget(headline)
        bkg_text = self.feature.attribute('bkg_text')
        self.review_layout.addWidget(QLabel(bkg_text))

    def _setup_preview_layer(self):
        '''
        set up the layer to show the results on
        '''
        self.preview_layer = QgsVectorLayer(
            f'Point?crs={self.crs}', 'results_tmp', 'memory')

        renderer = QgsCategorizedSymbolRenderer('i')
        for i in range(1, len(self.results) + 1):
            category = QgsRendererCategory()
            category.setValue(i)
            symbol = QgsMarkerSymbol.createSimple({'color': 'white'})
            img_path = os.path.join(ICON_PATH, f'marker_{i}.png')
            if os.path.exists(img_path):
                symbol_layer = QgsRasterMarkerSymbolLayer()
                symbol_layer.setPath(img_path)
                symbol_layer.setSize(5)
                symbol.appendSymbolLayer(symbol_layer)
            category.setSymbol(symbol)
            renderer.addCategory(category)
        self.preview_layer.setRenderer(renderer)

        self.preview_layer.startEditing()
        provider = self.preview_layer.dataProvider()
        provider.addAttributes([
            QgsField('i',  QVariant.Int),
            QgsField('text', QVariant.String)
        ])
        QgsProject.instance().addMapLayer(self.preview_layer)

    def _add_results(self, preselect: int = -1, row_number: int = 0):
        '''
        adds results to the map canvas and to the result list of the dialog
        '''
        provider = self.preview_layer.dataProvider()

        for i, result in enumerate(self.results):
            feature = QgsFeature()
            coords = result['geometry']['coordinates']
            geom = QgsGeometry.fromPointXY(QgsPointXY(coords[0], coords[1]))
            feature.setGeometry(geom)
            feature.setAttributes([i + 1, result['properties']['text'],])
            provider.addFeature(feature)

            properties = result['properties']
            radio = QRadioButton(properties['text'])

            preview = QLabel()
            preview.setMaximumWidth(20)
            preview.setMinimumWidth(20)
            self.results_contents.addWidget(preview, i+row_number, 0)
            self.results_contents.addWidget(radio, i+row_number, 1)
            if self.show_score:
                score = QLabel(f'Score {properties["score"]}')
                self.results_contents.addWidget(score, i+row_number, 2)
            img_path = os.path.join(ICON_PATH, f'marker_{i+1}.png')
            if os.path.exists(img_path):
                pixmap = QPixmap(img_path)
                preview.setPixmap(pixmap.scaled(
                    preview.size(), Qt.KeepAspectRatio,
                    Qt.SmoothTransformation))

            #  results clicked in the dialog are highlighted on the map
            radio.toggled.connect(
                lambda c, i=i, f=feature:
                self._toggle_result(i, f))
            if i == preselect:
                radio.setChecked(True)

        self.preview_layer.commitChanges()
        extent = self.preview_layer.extent()
        if not extent.isEmpty():
            transform = QgsCoordinateTransform(
                self.preview_layer.crs(),
                self.canvas.mapSettings().destinationCrs(),
                QgsProject.instance()
            )
            self.canvas.setExtent(transform.transform(extent))
        self.canvas.refresh()

    def _toggle_result(self, n, feature: QgsFeature):
        '''
        toggle the of the inspected feature, take the n-th result as
        currently picked result
        '''
        self.result = self.results[n]
        self.i = n
        self.preview_layer.removeSelection()
        self.preview_layer.select(feature.id())
        self._zoom_to(feature)

    def _zoom_to(self, feature):
        '''
        zoom to feature
        '''
        # center map on point
        point = feature.geometry().asPoint()
        rect = QgsRectangle(point, point)
        transform = QgsCoordinateTransform(
            self.preview_layer.crs(),
            self.canvas.mapSettings().destinationCrs(),
            QgsProject.instance()
        )
        self.canvas.setExtent(transform.transform(rect))
        self.canvas.refresh()

    def accept(self):
        '''
        override clicking ok button
        '''
        QgsProject.instance().removeMapLayer(self.preview_layer.id())
        super().accept()

    def reject(self):
        '''
        override clicking cancel button
        '''
        QgsProject.instance().removeMapLayer(self.preview_layer.id())
        super().reject()

    def showEvent(self, e):
        '''
        override, adjust size on opening dialog
        '''
        # exec() resets the modality
        self.setModal(False)
        self.adjustSize()


class ReverseResultsDialog(InspectResultsDialog):
    show_score = False
    '''
    dialog showing a feature with its attributes used for geocoding  and
    a list of pickable results of geocoding this feature
    dialog can be accepted (replace text and geometry with result), cancelled
    or just the geometry can be taken as a third option

    Attributes
    ----------
    geom_only : bool
        indicates whether third option was picked (take only the geometry)
        or not
    '''

    def __init__(self, feature: QgsFeature, results: List[dict],
                 canvas: QgsMapCanvas, review_fields: List[str] = [],
                 crs: str = 'EPSG:4326', parent: QWidget = None):
        '''
        Parameters
        ----------
        feature : QWidget
            the feature to show reverse geocoded results for
        results : list
            results of reverse geocoding to given feature to let user pick from,
            list of geojson features with "geometry" attribute and "properties"
            containing "text" (description of feature)
        canvas : QgsMapCanvas
            the map canvas to preview the results on
        review_fields : list, optional
            list of field names of the given feature whose values are shown in
            the dialog review section, defaults to empty list (no fields shown)
        crs : str, optional
            code of projection of the geometries of the given features (feature
            and results), defaults to epsg 4326
        parent : QWidget, optional
            parent widget, defaults to None
        '''
        super().__init__(feature, results, canvas, crs=crs,
                         review_fields=review_fields, parent=parent)
        # ui file was designed for the inspection of geocoding results,
        # replace labels to match reverse geocoding
        self.results_label.setText('Nächstgelegene Adressen')
        self.setWindowTitle('Nachbaradresssuche')
        self.accept_button.setText('Adresse und Koordinaten übernehmen')
        self.geom_only_button.setVisible(True)
        self.geom_only = False
        def geom_only():
            self.geom_only = True
            self.accept()
        self.geom_only_button.clicked.connect(geom_only)

    def _add_results(self, preselect: int = -1):
        '''
        override
        '''
        # add a radio button for the position the feature was dragged to
        point = self.feature.geometry().asPoint()
        radio_label = ('Koordinaten der Markierung '
                       f'({round(point.x(), 2)}, {round(point.y(), 2)})')
        radio = QRadioButton(radio_label)
        def toggled(checked):
            # radio is checked -> no result selected
            if checked:
                self.result = None
                self.i = -1
                self.preview_layer.removeSelection()
                self._zoom_to(self.feature)
            self.accept_button.setDisabled(checked)
        radio.toggled.connect(toggled)
        # initially this option is checked
        radio.setChecked(True)

        self.results_contents.addWidget(radio, 0, 1)
        super()._add_results(preselect=-1, row_number=1)

    def update_results(self, results: List[dict]):
        '''
        replace the currently listed results with the given ones

        Parameters
        ----------
        results : list
            results of reverse geocoding to given feature to let user pick from,
            list of geojson features with "geometry" attribute and "properties"
            containing "text" (description of feature)
        '''
        self.results = results
        # lazy way to reset preview
        QgsProject.instance().removeMapLayer(self.preview_layer.id())
        clear_layout(self.results_contents)
        self._setup_preview_layer()
        self._add_results()

