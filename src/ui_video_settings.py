# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'video_settings.ui'
##
## Created by: Qt User Interface Compiler version 6.12.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import *  # type: ignore
from PySide6.QtGui import *  # type: ignore
from PySide6.QtWidgets import *  # type: ignore

class Ui_Dialog(object):
    def setupUi(self, Dialog):
        if not Dialog.objectName():
            Dialog.setObjectName(u"Dialog")
        Dialog.resize(460, 420)
        self.gridLayout = QGridLayout(Dialog)
        self.gridLayout.setObjectName(u"gridLayout")
        self.label_captureHint = QLabel(Dialog)
        self.label_captureHint.setObjectName(u"label_captureHint")
        self.label_captureHint.setWordWrap(True)

        self.gridLayout.addWidget(self.label_captureHint, 0, 0, 1, 1)

        self.widget = QWidget(Dialog)
        self.widget.setObjectName(u"widget")
        self.formLayout = QFormLayout(self.widget)
        self.formLayout.setObjectName(u"formLayout")
        self.label = QLabel(self.widget)
        self.label.setObjectName(u"label")

        self.formLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.label)

        self.label_formatValue = QLabel(self.widget)
        self.label_formatValue.setObjectName(u"label_formatValue")
        self.label_formatValue.setWordWrap(True)

        self.formLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.label_formatValue)

        self.label_2 = QLabel(self.widget)
        self.label_2.setObjectName(u"label_2")

        self.formLayout.setWidget(3, QFormLayout.ItemRole.LabelRole, self.label_2)

        self.spinBox_fps = QSpinBox(self.widget)
        self.spinBox_fps.setObjectName(u"spinBox_fps")
        self.spinBox_fps.setMinimum(5)
        self.spinBox_fps.setMaximum(240)
        self.spinBox_fps.setValue(30)

        self.formLayout.setWidget(3, QFormLayout.ItemRole.FieldRole, self.spinBox_fps)

        self.label_fpsNote = QLabel(self.widget)
        self.label_fpsNote.setObjectName(u"label_fpsNote")
        self.label_fpsNote.setVisible(False)
        self.label_fpsNote.setWordWrap(True)

        self.formLayout.setWidget(6, QFormLayout.ItemRole.SpanningRole, self.label_fpsNote)

        self.label_3 = QLabel(self.widget)
        self.label_3.setObjectName(u"label_3")

        self.formLayout.setWidget(4, QFormLayout.ItemRole.LabelRole, self.label_3)

        self.comboBox_resolution = QComboBox(self.widget)
        self.comboBox_resolution.addItem("")
        self.comboBox_resolution.addItem(u"320x240")
        self.comboBox_resolution.addItem(u"640x480")
        self.comboBox_resolution.addItem(u"800x600")
        self.comboBox_resolution.addItem(u"1280x720")
        self.comboBox_resolution.addItem(u"1280x960")
        self.comboBox_resolution.addItem(u"1600x900")
        self.comboBox_resolution.addItem(u"1920x1080")
        self.comboBox_resolution.addItem(u"3840x2160")
        self.comboBox_resolution.addItem(u"2560x1440")
        self.comboBox_resolution.addItem(u"2048x1080")
        self.comboBox_resolution.addItem(u"4096x2160")
        self.comboBox_resolution.setObjectName(u"comboBox_resolution")

        self.formLayout.setWidget(4, QFormLayout.ItemRole.FieldRole, self.comboBox_resolution)

        self.plainTextEdit_videoProps = QPlainTextEdit(self.widget)
        self.plainTextEdit_videoProps.setObjectName(u"plainTextEdit_videoProps")
        self.plainTextEdit_videoProps.setReadOnly(True)

        self.formLayout.setWidget(5, QFormLayout.ItemRole.FieldRole, self.plainTextEdit_videoProps)

        self.label_4 = QLabel(self.widget)
        self.label_4.setObjectName(u"label_4")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.label_4)

        self.comboBox_captureBackend = QComboBox(self.widget)
        self.comboBox_captureBackend.addItem("")
        self.comboBox_captureBackend.setObjectName(u"comboBox_captureBackend")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.comboBox_captureBackend)


        self.gridLayout.addWidget(self.widget, 1, 0, 1, 1)

        self.buttonBox = QDialogButtonBox(Dialog)
        self.buttonBox.setObjectName(u"buttonBox")
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel|QDialogButtonBox.Ok)

        self.gridLayout.addWidget(self.buttonBox, 3, 0, 1, 1)


        self.retranslateUi(Dialog)
        self.buttonBox.accepted.connect(Dialog.accept)
        self.buttonBox.rejected.connect(Dialog.reject)

        QMetaObject.connectSlotsByName(Dialog)
    # setupUi

    def retranslateUi(self, Dialog):
        Dialog.setWindowTitle(QCoreApplication.translate("Dialog", u"Dialog", None))
        self.label_captureHint.setText(QCoreApplication.translate("Dialog", u"The video format is chosen by the camera driver and can't be changed here: some capture cards report a code that isn't a real format name. Details the device doesn't report are hidden below. If the picture is black, stuttering or the wrong size, switch the capture backend between DShow and Microsoft Media Foundation, and close other programs that are using the camera. The frame rate defaults to 30 \u2014 never 1.", None))
        self.label.setText(QCoreApplication.translate("Dialog", u"Video Format", None))
        self.label_formatValue.setText(QCoreApplication.translate("Dialog", u"Driver default (not editable)", None))
        self.label_2.setText(QCoreApplication.translate("Dialog", u"FPS", None))
#if QT_CONFIG(tooltip)
        self.spinBox_fps.setToolTip(QCoreApplication.translate("Dialog", u"30 is the default. 1 is not allowed: a capture card that doesn't report a frame rate used to be saved as 1.", None))
#endif // QT_CONFIG(tooltip)
        self.label_fpsNote.setText("")
        self.label_3.setText(QCoreApplication.translate("Dialog", u"Resolution", None))
        self.comboBox_resolution.setItemText(0, QCoreApplication.translate("Dialog", u"Default", None))

        self.label_4.setText(QCoreApplication.translate("Dialog", u"Capture Backend", None))
        self.comboBox_captureBackend.setItemText(0, QCoreApplication.translate("Dialog", u"Default / Any", None))

    # retranslateUi

