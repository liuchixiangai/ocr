# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'update_available.ui'
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
        Dialog.resize(424, 240)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(Dialog.sizePolicy().hasHeightForWidth())
        Dialog.setSizePolicy(sizePolicy)
        self.gridLayout = QGridLayout(Dialog)
#ifndef Q_OS_MACOS
        self.gridLayout.setSpacing(-1)
#endif
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setSizeConstraint(QLayout.SetFixedSize)
        self.label_newVersion = QLabel(Dialog)
        self.label_newVersion.setObjectName(u"label_newVersion")
        self.label_newVersion.setFrameShape(QFrame.NoFrame)
        self.label_newVersion.setTextFormat(Qt.RichText)
        self.label_newVersion.setWordWrap(True)
        self.label_newVersion.setMargin(0)
        self.label_newVersion.setIndent(-1)
        self.label_newVersion.setOpenExternalLinks(True)

        self.gridLayout.addWidget(self.label_newVersion, 0, 0, 1, 1)

        self.label_noNewVersion = QLabel(Dialog)
        self.label_noNewVersion.setObjectName(u"label_noNewVersion")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.label_noNewVersion.sizePolicy().hasHeightForWidth())
        self.label_noNewVersion.setSizePolicy(sizePolicy1)
        self.label_noNewVersion.setFrameShape(QFrame.NoFrame)
        self.label_noNewVersion.setTextFormat(Qt.RichText)
        self.label_noNewVersion.setAlignment(Qt.AlignLeading|Qt.AlignLeft|Qt.AlignTop)
        self.label_noNewVersion.setWordWrap(True)
        self.label_noNewVersion.setMargin(0)
        self.label_noNewVersion.setIndent(-1)
        self.label_noNewVersion.setOpenExternalLinks(True)

        self.gridLayout.addWidget(self.label_noNewVersion, 1, 0, 1, 1)

        self.label_error = QLabel(Dialog)
        self.label_error.setObjectName(u"label_error")
        self.label_error.setTextFormat(Qt.RichText)
        self.label_error.setWordWrap(True)
        self.label_error.setOpenExternalLinks(True)

        self.gridLayout.addWidget(self.label_error, 2, 0, 1, 1)

        self.label_versions = QLabel(Dialog)
        self.label_versions.setObjectName(u"label_versions")
        self.label_versions.setTextFormat(Qt.PlainText)
        self.label_versions.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.gridLayout.addWidget(self.label_versions, 3, 0, 1, 1)

        self.line = QFrame(Dialog)
        self.line.setObjectName(u"line")
        self.line.setFrameShape(QFrame.Shape.HLine)
        self.line.setFrameShadow(QFrame.Shadow.Sunken)

        self.gridLayout.addWidget(self.line, 4, 0, 1, 1)

        self.checkBox_disableUpdateChecks = QCheckBox(Dialog)
        self.checkBox_disableUpdateChecks.setObjectName(u"checkBox_disableUpdateChecks")

        self.gridLayout.addWidget(self.checkBox_disableUpdateChecks, 5, 0, 1, 1)

        self.buttonBox = QDialogButtonBox(Dialog)
        self.buttonBox.setObjectName(u"buttonBox")
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Ok)

        self.gridLayout.addWidget(self.buttonBox, 6, 0, 1, 1)


        self.retranslateUi(Dialog)
        self.buttonBox.accepted.connect(Dialog.accept)
        self.buttonBox.rejected.connect(Dialog.reject)

        QMetaObject.connectSlotsByName(Dialog)
    # setupUi

    def retranslateUi(self, Dialog):
        Dialog.setWindowTitle(QCoreApplication.translate("Dialog", u"Update Available", None))
        self.label_newVersion.setText(QCoreApplication.translate("Dialog", u"<html><head/><body><p>A new version of ScoreSight is available!</p><p>Download it from <a href=\"https://github.com/royshil/scoresight/releases/latest\">GitHub Releases</a>.</p><p>Your settings carry over to the new version.</p></body></html>", None))
        self.label_noNewVersion.setText(QCoreApplication.translate("Dialog", u"<html><head/><body><p>You are running the latest version of ScoreSight.</p><p>All versions are available on <a href=\"https://github.com/royshil/scoresight/releases\">GitHub Releases</a>.</p></body></html>", None))
        self.label_error.setText(QCoreApplication.translate("Dialog", u"<html><head/><body><p>Could not check for updates. Check your internet connection, or look for new versions on <a href=\"https://github.com/royshil/scoresight/releases/latest\">GitHub Releases</a>.</p></body></html>", None))
        self.checkBox_disableUpdateChecks.setText(QCoreApplication.translate("Dialog", u"Don't check for updates at startup", None))
    # retranslateUi

