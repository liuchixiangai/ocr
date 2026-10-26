# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'connect_obs.ui'
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
        Dialog.resize(360, 180)
        self.verticalLayout = QVBoxLayout(Dialog)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.groupBox = QGroupBox(Dialog)
        self.groupBox.setObjectName(u"groupBox")
        self.gridLayout = QGridLayout(self.groupBox)
        self.gridLayout.setObjectName(u"gridLayout")
        self.formLayout = QFormLayout()
        self.formLayout.setObjectName(u"formLayout")
        self.lineEdit_ip = QLineEdit(self.groupBox)
        self.lineEdit_ip.setObjectName(u"lineEdit_ip")
        self.lineEdit_ip.setText(u"localhost")

        self.formLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.lineEdit_ip)

        self.label = QLabel(self.groupBox)
        self.label.setObjectName(u"label")

        self.formLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.label)

        self.label_2 = QLabel(self.groupBox)
        self.label_2.setObjectName(u"label_2")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.label_2)

        self.lineEdit_port = QLineEdit(self.groupBox)
        self.lineEdit_port.setObjectName(u"lineEdit_port")
        self.lineEdit_port.setText(u"4455")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.lineEdit_port)

        self.label_3 = QLabel(self.groupBox)
        self.label_3.setObjectName(u"label_3")

        self.formLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.label_3)

        self.lineEdit_password = QLineEdit(self.groupBox)
        self.lineEdit_password.setObjectName(u"lineEdit_password")
        self.lineEdit_password.setEchoMode(QLineEdit.Password)

        self.formLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.lineEdit_password)

        self.checkBox_showPassword = QCheckBox(self.groupBox)
        self.checkBox_showPassword.setObjectName(u"checkBox_showPassword")

        self.formLayout.setWidget(3, QFormLayout.ItemRole.FieldRole, self.checkBox_showPassword)


        self.gridLayout.addLayout(self.formLayout, 0, 0, 1, 1)


        self.verticalLayout.addWidget(self.groupBox)

        self.horizontalLayout_buttons = QHBoxLayout()
        self.horizontalLayout_buttons.setObjectName(u"horizontalLayout_buttons")
        self.horizontalSpacer_buttons = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_buttons.addItem(self.horizontalSpacer_buttons)

        self.pushButton_connect = QPushButton(Dialog)
        self.pushButton_connect.setObjectName(u"pushButton_connect")

        self.horizontalLayout_buttons.addWidget(self.pushButton_connect)

        self.pushButton_cancel = QPushButton(Dialog)
        self.pushButton_cancel.setObjectName(u"pushButton_cancel")
        self.pushButton_cancel.setAutoDefault(False)

        self.horizontalLayout_buttons.addWidget(self.pushButton_cancel)


        self.verticalLayout.addLayout(self.horizontalLayout_buttons)

        self.label_error = QLabel(Dialog)
        self.label_error.setObjectName(u"label_error")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.label_error.sizePolicy().hasHeightForWidth())
        self.label_error.setSizePolicy(sizePolicy)

        self.verticalLayout.addWidget(self.label_error)


        self.retranslateUi(Dialog)
        self.pushButton_cancel.clicked.connect(Dialog.reject)

        self.pushButton_connect.setDefault(True)


        QMetaObject.connectSlotsByName(Dialog)
    # setupUi

    def retranslateUi(self, Dialog):
        Dialog.setWindowTitle(QCoreApplication.translate("Dialog", u"Connect OBS", None))
        self.groupBox.setTitle(QCoreApplication.translate("Dialog", u"Websocket Server", None))
        self.label.setText(QCoreApplication.translate("Dialog", u"IP", None))
        self.label_2.setText(QCoreApplication.translate("Dialog", u"Port", None))
        self.label_3.setText(QCoreApplication.translate("Dialog", u"Password", None))
        self.checkBox_showPassword.setText(QCoreApplication.translate("Dialog", u"Show password", None))
        self.pushButton_connect.setText(QCoreApplication.translate("Dialog", u"Connect", None))
        self.pushButton_cancel.setText(QCoreApplication.translate("Dialog", u"Cancel", None))
        self.label_error.setText("")
    # retranslateUi

