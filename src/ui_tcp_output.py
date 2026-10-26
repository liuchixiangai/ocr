# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'tcp_output.ui'
##
## Created by: Qt User Interface Compiler version 6.12.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import *  # type: ignore
from PySide6.QtGui import *  # type: ignore
from PySide6.QtWidgets import *  # type: ignore

class Ui_TcpOutputPage(object):
    def setupUi(self, TcpOutputPage):
        if not TcpOutputPage.objectName():
            TcpOutputPage.setObjectName(u"TcpOutputPage")
        TcpOutputPage.resize(330, 900)
        self.verticalLayout_tcpPage = QVBoxLayout(TcpOutputPage)
        self.verticalLayout_tcpPage.setSpacing(0)
        self.verticalLayout_tcpPage.setObjectName(u"verticalLayout_tcpPage")
        self.verticalLayout_tcpPage.setContentsMargins(0, 0, 0, 0)
        self.scrollArea_tcp = QScrollArea(TcpOutputPage)
        self.scrollArea_tcp.setObjectName(u"scrollArea_tcp")
        self.scrollArea_tcp.setFrameShape(QFrame.NoFrame)
        self.scrollArea_tcp.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollArea_tcp.setWidgetResizable(True)
        self.scrollAreaWidgetContents_tcp = QWidget()
        self.scrollAreaWidgetContents_tcp.setObjectName(u"scrollAreaWidgetContents_tcp")
        self.formLayout_tcp = QFormLayout(self.scrollAreaWidgetContents_tcp)
        self.formLayout_tcp.setObjectName(u"formLayout_tcp")
        self.formLayout_tcp.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.formLayout_tcp.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.formLayout_tcp.setHorizontalSpacing(6)
        self.formLayout_tcp.setVerticalSpacing(4)
        self.formLayout_tcp.setContentsMargins(6, 6, 6, 6)
        self.label_tcpPreset = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpPreset.setObjectName(u"label_tcpPreset")

        self.formLayout_tcp.setWidget(0, QFormLayout.ItemRole.LabelRole, self.label_tcpPreset)

        self.widget_tcpPresetRow = QWidget(self.scrollAreaWidgetContents_tcp)
        self.widget_tcpPresetRow.setObjectName(u"widget_tcpPresetRow")
        self.horizontalLayout_tcpPresetRow = QHBoxLayout(self.widget_tcpPresetRow)
        self.horizontalLayout_tcpPresetRow.setSpacing(3)
        self.horizontalLayout_tcpPresetRow.setObjectName(u"horizontalLayout_tcpPresetRow")
        self.horizontalLayout_tcpPresetRow.setContentsMargins(0, 0, 0, 0)
        self.comboBox_tcpPreset = QComboBox(self.widget_tcpPresetRow)
        self.comboBox_tcpPreset.setObjectName(u"comboBox_tcpPreset")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sizePolicy.setHorizontalStretch(1)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.comboBox_tcpPreset.sizePolicy().hasHeightForWidth())
        self.comboBox_tcpPreset.setSizePolicy(sizePolicy)
        self.comboBox_tcpPreset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.comboBox_tcpPreset.setMinimumContentsLength(8)

        self.horizontalLayout_tcpPresetRow.addWidget(self.comboBox_tcpPreset)

        self.toolButton_tcpResetPreset = QToolButton(self.widget_tcpPresetRow)
        self.toolButton_tcpResetPreset.setObjectName(u"toolButton_tcpResetPreset")

        self.horizontalLayout_tcpPresetRow.addWidget(self.toolButton_tcpResetPreset)


        self.formLayout_tcp.setWidget(0, QFormLayout.ItemRole.FieldRole, self.widget_tcpPresetRow)

        self.label_tcpPresetNote = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpPresetNote.setObjectName(u"label_tcpPresetNote")
        self.label_tcpPresetNote.setWordWrap(True)

        self.formLayout_tcp.setWidget(1, QFormLayout.ItemRole.SpanningRole, self.label_tcpPresetNote)

        self.label_tcpAddress = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpAddress.setObjectName(u"label_tcpAddress")

        self.formLayout_tcp.setWidget(2, QFormLayout.ItemRole.LabelRole, self.label_tcpAddress)

        self.widget_tcpAddressRow = QWidget(self.scrollAreaWidgetContents_tcp)
        self.widget_tcpAddressRow.setObjectName(u"widget_tcpAddressRow")
        self.horizontalLayout_tcpAddressRow = QHBoxLayout(self.widget_tcpAddressRow)
        self.horizontalLayout_tcpAddressRow.setSpacing(3)
        self.horizontalLayout_tcpAddressRow.setObjectName(u"horizontalLayout_tcpAddressRow")
        self.horizontalLayout_tcpAddressRow.setContentsMargins(0, 0, 0, 0)
        self.lineEdit_tcpUrl = QLineEdit(self.widget_tcpAddressRow)
        self.lineEdit_tcpUrl.setObjectName(u"lineEdit_tcpUrl")
        self.lineEdit_tcpUrl.setMinimumSize(QSize(60, 0))
        self.lineEdit_tcpUrl.setPlaceholderText(u"wss://example.com/scores")

        self.horizontalLayout_tcpAddressRow.addWidget(self.lineEdit_tcpUrl)

        self.lineEdit_tcpHost = QLineEdit(self.widget_tcpAddressRow)
        self.lineEdit_tcpHost.setObjectName(u"lineEdit_tcpHost")
        self.lineEdit_tcpHost.setMinimumSize(QSize(60, 0))
        self.lineEdit_tcpHost.setText(u"localhost")

        self.horizontalLayout_tcpAddressRow.addWidget(self.lineEdit_tcpHost)

        self.label_tcpColon = QLabel(self.widget_tcpAddressRow)
        self.label_tcpColon.setObjectName(u"label_tcpColon")
        self.label_tcpColon.setText(u":")

        self.horizontalLayout_tcpAddressRow.addWidget(self.label_tcpColon)

        self.lineEdit_tcpPort = QLineEdit(self.widget_tcpAddressRow)
        self.lineEdit_tcpPort.setObjectName(u"lineEdit_tcpPort")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.lineEdit_tcpPort.sizePolicy().hasHeightForWidth())
        self.lineEdit_tcpPort.setSizePolicy(sizePolicy1)
        self.lineEdit_tcpPort.setMaximumSize(QSize(52, 16777215))

        self.horizontalLayout_tcpAddressRow.addWidget(self.lineEdit_tcpPort)

        self.comboBox_tcpTransport = QComboBox(self.widget_tcpAddressRow)
        self.comboBox_tcpTransport.addItem(u"TCP")
        self.comboBox_tcpTransport.addItem(u"UDP")
        self.comboBox_tcpTransport.addItem("")
        self.comboBox_tcpTransport.addItem(u"WebSocket")
        self.comboBox_tcpTransport.setObjectName(u"comboBox_tcpTransport")
        sizePolicy1.setHeightForWidth(self.comboBox_tcpTransport.sizePolicy().hasHeightForWidth())
        self.comboBox_tcpTransport.setSizePolicy(sizePolicy1)

        self.horizontalLayout_tcpAddressRow.addWidget(self.comboBox_tcpTransport)


        self.formLayout_tcp.setWidget(2, QFormLayout.ItemRole.FieldRole, self.widget_tcpAddressRow)

        self.label_tcpHeaders = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpHeaders.setObjectName(u"label_tcpHeaders")

        self.formLayout_tcp.setWidget(3, QFormLayout.ItemRole.LabelRole, self.label_tcpHeaders)

        self.plainTextEdit_tcpHeaders = QPlainTextEdit(self.scrollAreaWidgetContents_tcp)
        self.plainTextEdit_tcpHeaders.setObjectName(u"plainTextEdit_tcpHeaders")
        self.plainTextEdit_tcpHeaders.setMaximumSize(QSize(16777215, 52))
        self.plainTextEdit_tcpHeaders.setTabChangesFocus(True)
        self.plainTextEdit_tcpHeaders.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.plainTextEdit_tcpHeaders.setPlaceholderText(u"Authorization: Bearer \u2026")

        self.formLayout_tcp.setWidget(3, QFormLayout.ItemRole.FieldRole, self.plainTextEdit_tcpHeaders)

        self.widget_tcpStartRow = QWidget(self.scrollAreaWidgetContents_tcp)
        self.widget_tcpStartRow.setObjectName(u"widget_tcpStartRow")
        self.horizontalLayout_tcpStartRow = QHBoxLayout(self.widget_tcpStartRow)
        self.horizontalLayout_tcpStartRow.setSpacing(6)
        self.horizontalLayout_tcpStartRow.setObjectName(u"horizontalLayout_tcpStartRow")
        self.horizontalLayout_tcpStartRow.setContentsMargins(0, 0, 0, 0)
        self.pushButton_tcpStart = QPushButton(self.widget_tcpStartRow)
        self.pushButton_tcpStart.setObjectName(u"pushButton_tcpStart")
        sizePolicy1.setHeightForWidth(self.pushButton_tcpStart.sizePolicy().hasHeightForWidth())
        self.pushButton_tcpStart.setSizePolicy(sizePolicy1)
        self.pushButton_tcpStart.setText(u"Start sending")
        self.pushButton_tcpStart.setCheckable(True)

        self.horizontalLayout_tcpStartRow.addWidget(self.pushButton_tcpStart)

        self.horizontalSpacer_tcpStart = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_tcpStartRow.addItem(self.horizontalSpacer_tcpStart)


        self.formLayout_tcp.setWidget(4, QFormLayout.ItemRole.SpanningRole, self.widget_tcpStartRow)

        self.label_tcpReply = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpReply.setObjectName(u"label_tcpReply")
        self.label_tcpReply.setWordWrap(True)
        self.label_tcpReply.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.formLayout_tcp.setWidget(5, QFormLayout.ItemRole.SpanningRole, self.label_tcpReply)

        self.groupBox_tcpCaspar = QGroupBox(self.scrollAreaWidgetContents_tcp)
        self.groupBox_tcpCaspar.setObjectName(u"groupBox_tcpCaspar")
        self.groupBox_tcpCaspar.setTitle(u"CasparCG")
        self.formLayout_tcpCaspar = QFormLayout(self.groupBox_tcpCaspar)
        self.formLayout_tcpCaspar.setObjectName(u"formLayout_tcpCaspar")
        self.formLayout_tcpCaspar.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.formLayout_tcpCaspar.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.formLayout_tcpCaspar.setVerticalSpacing(3)
        self.formLayout_tcpCaspar.setContentsMargins(6, 6, 6, 6)
        self.label_tcpChannel = QLabel(self.groupBox_tcpCaspar)
        self.label_tcpChannel.setObjectName(u"label_tcpChannel")

        self.formLayout_tcpCaspar.setWidget(0, QFormLayout.ItemRole.LabelRole, self.label_tcpChannel)

        self.spinBox_tcpChannel = QSpinBox(self.groupBox_tcpCaspar)
        self.spinBox_tcpChannel.setObjectName(u"spinBox_tcpChannel")
        self.spinBox_tcpChannel.setMinimum(1)
        self.spinBox_tcpChannel.setMaximum(9999)

        self.formLayout_tcpCaspar.setWidget(0, QFormLayout.ItemRole.FieldRole, self.spinBox_tcpChannel)

        self.label_tcpLayer = QLabel(self.groupBox_tcpCaspar)
        self.label_tcpLayer.setObjectName(u"label_tcpLayer")

        self.formLayout_tcpCaspar.setWidget(1, QFormLayout.ItemRole.LabelRole, self.label_tcpLayer)

        self.spinBox_tcpLayer = QSpinBox(self.groupBox_tcpCaspar)
        self.spinBox_tcpLayer.setObjectName(u"spinBox_tcpLayer")
        self.spinBox_tcpLayer.setMinimum(-9999)
        self.spinBox_tcpLayer.setMaximum(9999)

        self.formLayout_tcpCaspar.setWidget(1, QFormLayout.ItemRole.FieldRole, self.spinBox_tcpLayer)

        self.label_tcpCgLayer = QLabel(self.groupBox_tcpCaspar)
        self.label_tcpCgLayer.setObjectName(u"label_tcpCgLayer")

        self.formLayout_tcpCaspar.setWidget(2, QFormLayout.ItemRole.LabelRole, self.label_tcpCgLayer)

        self.spinBox_tcpCgLayer = QSpinBox(self.groupBox_tcpCaspar)
        self.spinBox_tcpCgLayer.setObjectName(u"spinBox_tcpCgLayer")
        self.spinBox_tcpCgLayer.setMaximum(9999)

        self.formLayout_tcpCaspar.setWidget(2, QFormLayout.ItemRole.FieldRole, self.spinBox_tcpCgLayer)

        self.label_tcpTemplateName = QLabel(self.groupBox_tcpCaspar)
        self.label_tcpTemplateName.setObjectName(u"label_tcpTemplateName")

        self.formLayout_tcpCaspar.setWidget(3, QFormLayout.ItemRole.LabelRole, self.label_tcpTemplateName)

        self.lineEdit_tcpTemplateName = QLineEdit(self.groupBox_tcpCaspar)
        self.lineEdit_tcpTemplateName.setObjectName(u"lineEdit_tcpTemplateName")

        self.formLayout_tcpCaspar.setWidget(3, QFormLayout.ItemRole.FieldRole, self.lineEdit_tcpTemplateName)

        self.widget_tcpCasparButtons = QWidget(self.groupBox_tcpCaspar)
        self.widget_tcpCasparButtons.setObjectName(u"widget_tcpCasparButtons")
        self.horizontalLayout_tcpCasparButtons = QHBoxLayout(self.widget_tcpCasparButtons)
        self.horizontalLayout_tcpCasparButtons.setSpacing(3)
        self.horizontalLayout_tcpCasparButtons.setObjectName(u"horizontalLayout_tcpCasparButtons")
        self.horizontalLayout_tcpCasparButtons.setContentsMargins(0, 0, 0, 0)
        self.pushButton_tcpLoad = QPushButton(self.widget_tcpCasparButtons)
        self.pushButton_tcpLoad.setObjectName(u"pushButton_tcpLoad")

        self.horizontalLayout_tcpCasparButtons.addWidget(self.pushButton_tcpLoad)

        self.pushButton_tcpPlay = QPushButton(self.widget_tcpCasparButtons)
        self.pushButton_tcpPlay.setObjectName(u"pushButton_tcpPlay")

        self.horizontalLayout_tcpCasparButtons.addWidget(self.pushButton_tcpPlay)

        self.pushButton_tcpStop = QPushButton(self.widget_tcpCasparButtons)
        self.pushButton_tcpStop.setObjectName(u"pushButton_tcpStop")

        self.horizontalLayout_tcpCasparButtons.addWidget(self.pushButton_tcpStop)


        self.formLayout_tcpCaspar.setWidget(4, QFormLayout.ItemRole.SpanningRole, self.widget_tcpCasparButtons)

        self.pushButton_tcpCopyTemplate = QPushButton(self.groupBox_tcpCaspar)
        self.pushButton_tcpCopyTemplate.setObjectName(u"pushButton_tcpCopyTemplate")

        self.formLayout_tcpCaspar.setWidget(5, QFormLayout.ItemRole.SpanningRole, self.pushButton_tcpCopyTemplate)


        self.formLayout_tcp.setWidget(6, QFormLayout.ItemRole.SpanningRole, self.groupBox_tcpCaspar)

        self.label_tcpMode = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpMode.setObjectName(u"label_tcpMode")

        self.formLayout_tcp.setWidget(7, QFormLayout.ItemRole.LabelRole, self.label_tcpMode)

        self.comboBox_tcpMode = QComboBox(self.scrollAreaWidgetContents_tcp)
        self.comboBox_tcpMode.setObjectName(u"comboBox_tcpMode")
        self.comboBox_tcpMode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.comboBox_tcpMode.setMinimumContentsLength(8)

        self.formLayout_tcp.setWidget(7, QFormLayout.ItemRole.FieldRole, self.comboBox_tcpMode)

        self.label_tcpTemplate = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpTemplate.setObjectName(u"label_tcpTemplate")

        self.formLayout_tcp.setWidget(8, QFormLayout.ItemRole.LabelRole, self.label_tcpTemplate)

        self.plainTextEdit_tcpTemplate = QPlainTextEdit(self.scrollAreaWidgetContents_tcp)
        self.plainTextEdit_tcpTemplate.setObjectName(u"plainTextEdit_tcpTemplate")
        self.plainTextEdit_tcpTemplate.setMinimumSize(QSize(0, 40))
        self.plainTextEdit_tcpTemplate.setMaximumSize(QSize(16777215, 72))
        self.plainTextEdit_tcpTemplate.setTabChangesFocus(True)

        self.formLayout_tcp.setWidget(8, QFormLayout.ItemRole.FieldRole, self.plainTextEdit_tcpTemplate)

        self.label_tcpInit = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpInit.setObjectName(u"label_tcpInit")

        self.formLayout_tcp.setWidget(9, QFormLayout.ItemRole.LabelRole, self.label_tcpInit)

        self.lineEdit_tcpInit = QLineEdit(self.scrollAreaWidgetContents_tcp)
        self.lineEdit_tcpInit.setObjectName(u"lineEdit_tcpInit")

        self.formLayout_tcp.setWidget(9, QFormLayout.ItemRole.FieldRole, self.lineEdit_tcpInit)

        self.label_tcpPreview = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpPreview.setObjectName(u"label_tcpPreview")

        self.formLayout_tcp.setWidget(10, QFormLayout.ItemRole.LabelRole, self.label_tcpPreview)

        self.plainTextEdit_tcpPreview = QPlainTextEdit(self.scrollAreaWidgetContents_tcp)
        self.plainTextEdit_tcpPreview.setObjectName(u"plainTextEdit_tcpPreview")
        self.plainTextEdit_tcpPreview.setMinimumSize(QSize(0, 40))
        self.plainTextEdit_tcpPreview.setMaximumSize(QSize(16777215, 96))
        self.plainTextEdit_tcpPreview.setTabChangesFocus(True)
        self.plainTextEdit_tcpPreview.setReadOnly(True)

        self.formLayout_tcp.setWidget(10, QFormLayout.ItemRole.FieldRole, self.plainTextEdit_tcpPreview)

        self.label_tcpLineEnding = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpLineEnding.setObjectName(u"label_tcpLineEnding")

        self.formLayout_tcp.setWidget(11, QFormLayout.ItemRole.LabelRole, self.label_tcpLineEnding)

        self.comboBox_tcpLineEnding = QComboBox(self.scrollAreaWidgetContents_tcp)
        self.comboBox_tcpLineEnding.setObjectName(u"comboBox_tcpLineEnding")
        self.comboBox_tcpLineEnding.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.comboBox_tcpLineEnding.setMinimumContentsLength(8)

        self.formLayout_tcp.setWidget(11, QFormLayout.ItemRole.FieldRole, self.comboBox_tcpLineEnding)

        self.label_tcpEncoding = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpEncoding.setObjectName(u"label_tcpEncoding")

        self.formLayout_tcp.setWidget(12, QFormLayout.ItemRole.LabelRole, self.label_tcpEncoding)

        self.comboBox_tcpEncoding = QComboBox(self.scrollAreaWidgetContents_tcp)
        self.comboBox_tcpEncoding.setObjectName(u"comboBox_tcpEncoding")
        self.comboBox_tcpEncoding.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.comboBox_tcpEncoding.setMinimumContentsLength(8)

        self.formLayout_tcp.setWidget(12, QFormLayout.ItemRole.FieldRole, self.comboBox_tcpEncoding)

        self.label_tcpEscape = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpEscape.setObjectName(u"label_tcpEscape")

        self.formLayout_tcp.setWidget(13, QFormLayout.ItemRole.LabelRole, self.label_tcpEscape)

        self.comboBox_tcpEscape = QComboBox(self.scrollAreaWidgetContents_tcp)
        self.comboBox_tcpEscape.setObjectName(u"comboBox_tcpEscape")
        self.comboBox_tcpEscape.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.comboBox_tcpEscape.setMinimumContentsLength(8)

        self.formLayout_tcp.setWidget(13, QFormLayout.ItemRole.FieldRole, self.comboBox_tcpEscape)

        self.checkBox_tcpSendSame = QCheckBox(self.scrollAreaWidgetContents_tcp)
        self.checkBox_tcpSendSame.setObjectName(u"checkBox_tcpSendSame")
        self.checkBox_tcpSendSame.setText(u"Also resend unchanged values")

        self.formLayout_tcp.setWidget(14, QFormLayout.ItemRole.SpanningRole, self.checkBox_tcpSendSame)

        self.label_tcpMinInterval = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpMinInterval.setObjectName(u"label_tcpMinInterval")

        self.formLayout_tcp.setWidget(15, QFormLayout.ItemRole.LabelRole, self.label_tcpMinInterval)

        self.doubleSpinBox_tcpMinInterval = QDoubleSpinBox(self.scrollAreaWidgetContents_tcp)
        self.doubleSpinBox_tcpMinInterval.setObjectName(u"doubleSpinBox_tcpMinInterval")
        self.doubleSpinBox_tcpMinInterval.setSuffix(u" s")
        self.doubleSpinBox_tcpMinInterval.setDecimals(2)
        self.doubleSpinBox_tcpMinInterval.setMaximum(10.000000000000000)
        self.doubleSpinBox_tcpMinInterval.setSingleStep(0.050000000000000)

        self.formLayout_tcp.setWidget(15, QFormLayout.ItemRole.FieldRole, self.doubleSpinBox_tcpMinInterval)

        self.label_tcpRefresh = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpRefresh.setObjectName(u"label_tcpRefresh")

        self.formLayout_tcp.setWidget(16, QFormLayout.ItemRole.LabelRole, self.label_tcpRefresh)

        self.doubleSpinBox_tcpRefresh = QDoubleSpinBox(self.scrollAreaWidgetContents_tcp)
        self.doubleSpinBox_tcpRefresh.setObjectName(u"doubleSpinBox_tcpRefresh")
        self.doubleSpinBox_tcpRefresh.setSuffix(u" s")
        self.doubleSpinBox_tcpRefresh.setDecimals(1)
        self.doubleSpinBox_tcpRefresh.setMaximum(3600.000000000000000)
        self.doubleSpinBox_tcpRefresh.setSingleStep(1.000000000000000)

        self.formLayout_tcp.setWidget(16, QFormLayout.ItemRole.FieldRole, self.doubleSpinBox_tcpRefresh)

        self.label_tcpNames = QLabel(self.scrollAreaWidgetContents_tcp)
        self.label_tcpNames.setObjectName(u"label_tcpNames")
        self.label_tcpNames.setWordWrap(True)

        self.formLayout_tcp.setWidget(17, QFormLayout.ItemRole.SpanningRole, self.label_tcpNames)

        self.tableView_tcpMapping = QTableView(self.scrollAreaWidgetContents_tcp)
        self.tableView_tcpMapping.setObjectName(u"tableView_tcpMapping")
        self.tableView_tcpMapping.setMinimumSize(QSize(0, 140))
        self.tableView_tcpMapping.horizontalHeader().setVisible(False)
        self.tableView_tcpMapping.horizontalHeader().setStretchLastSection(True)

        self.formLayout_tcp.setWidget(18, QFormLayout.ItemRole.SpanningRole, self.tableView_tcpMapping)

        self.widget_tcpNamesButtons = QWidget(self.scrollAreaWidgetContents_tcp)
        self.widget_tcpNamesButtons.setObjectName(u"widget_tcpNamesButtons")
        self.horizontalLayout_tcpNamesButtons = QHBoxLayout(self.widget_tcpNamesButtons)
        self.horizontalLayout_tcpNamesButtons.setObjectName(u"horizontalLayout_tcpNamesButtons")
        self.horizontalLayout_tcpNamesButtons.setContentsMargins(0, 0, 0, 0)
        self.toolButton_tcpResetNames = QToolButton(self.widget_tcpNamesButtons)
        self.toolButton_tcpResetNames.setObjectName(u"toolButton_tcpResetNames")

        self.horizontalLayout_tcpNamesButtons.addWidget(self.toolButton_tcpResetNames)

        self.horizontalSpacer_tcpNames = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_tcpNamesButtons.addItem(self.horizontalSpacer_tcpNames)


        self.formLayout_tcp.setWidget(19, QFormLayout.ItemRole.SpanningRole, self.widget_tcpNamesButtons)

        self.scrollArea_tcp.setWidget(self.scrollAreaWidgetContents_tcp)

        self.verticalLayout_tcpPage.addWidget(self.scrollArea_tcp)

#if QT_CONFIG(shortcut)
        self.label_tcpPreset.setBuddy(self.comboBox_tcpPreset)
        self.label_tcpAddress.setBuddy(self.lineEdit_tcpHost)
        self.label_tcpHeaders.setBuddy(self.plainTextEdit_tcpHeaders)
        self.label_tcpChannel.setBuddy(self.spinBox_tcpChannel)
        self.label_tcpLayer.setBuddy(self.spinBox_tcpLayer)
        self.label_tcpCgLayer.setBuddy(self.spinBox_tcpCgLayer)
        self.label_tcpTemplateName.setBuddy(self.lineEdit_tcpTemplateName)
        self.label_tcpMode.setBuddy(self.comboBox_tcpMode)
        self.label_tcpTemplate.setBuddy(self.plainTextEdit_tcpTemplate)
        self.label_tcpInit.setBuddy(self.lineEdit_tcpInit)
        self.label_tcpPreview.setBuddy(self.plainTextEdit_tcpPreview)
        self.label_tcpLineEnding.setBuddy(self.comboBox_tcpLineEnding)
        self.label_tcpEncoding.setBuddy(self.comboBox_tcpEncoding)
        self.label_tcpEscape.setBuddy(self.comboBox_tcpEscape)
        self.label_tcpMinInterval.setBuddy(self.doubleSpinBox_tcpMinInterval)
        self.label_tcpRefresh.setBuddy(self.doubleSpinBox_tcpRefresh)
#endif // QT_CONFIG(shortcut)

        self.retranslateUi(TcpOutputPage)

        QMetaObject.connectSlotsByName(TcpOutputPage)
    # setupUi

    def retranslateUi(self, TcpOutputPage):
        self.label_tcpPreset.setText(QCoreApplication.translate("TcpOutputPage", u"Preset", None))
#if QT_CONFIG(tooltip)
        self.comboBox_tcpPreset.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The receiving program. A preset sets the port, the template and the options for it; you can change them all.", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.toolButton_tcpResetPreset.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Set the port, the template and the options of the preset again", None))
#endif // QT_CONFIG(tooltip)
        self.toolButton_tcpResetPreset.setText(QCoreApplication.translate("TcpOutputPage", u"Reset", None))
        self.label_tcpAddress.setText(QCoreApplication.translate("TcpOutputPage", u"Address", None))
#if QT_CONFIG(accessibility)
        self.lineEdit_tcpUrl.setAccessibleName(QCoreApplication.translate("TcpOutputPage", u"WebSocket URL", None))
#endif // QT_CONFIG(accessibility)
#if QT_CONFIG(tooltip)
        self.lineEdit_tcpUrl.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The WebSocket address of the receiving server (ws:// or wss://). A token can go in the URL or in Headers", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(accessibility)
        self.lineEdit_tcpHost.setAccessibleName(QCoreApplication.translate("TcpOutputPage", u"Host", None))
#endif // QT_CONFIG(accessibility)
#if QT_CONFIG(tooltip)
        self.lineEdit_tcpHost.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The address of the receiving program's computer (localhost if it runs on this computer)", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(accessibility)
        self.lineEdit_tcpPort.setAccessibleName(QCoreApplication.translate("TcpOutputPage", u"Port", None))
#endif // QT_CONFIG(accessibility)
#if QT_CONFIG(tooltip)
        self.lineEdit_tcpPort.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The port the receiving program listens on (set in that program)", None))
#endif // QT_CONFIG(tooltip)
        self.lineEdit_tcpPort.setPlaceholderText(QCoreApplication.translate("TcpOutputPage", u"Port", None))
        self.comboBox_tcpTransport.setItemText(2, QCoreApplication.translate("TcpOutputPage", u"TCP server", None))

#if QT_CONFIG(accessibility)
        self.comboBox_tcpTransport.setAccessibleName(QCoreApplication.translate("TcpOutputPage", u"Transport", None))
#endif // QT_CONFIG(accessibility)
#if QT_CONFIG(tooltip)
        self.comboBox_tcpTransport.setToolTip(QCoreApplication.translate("TcpOutputPage", u"TCP connects to the receiving program and keeps the connection open. UDP sends each message as a datagram, without a connection. TCP server: ScoreSight listens on the port, and every program that connects to it gets the messages. WebSocket connects to a ws:// or wss:// URL and sends each message as a WebSocket message.", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpHeaders.setText(QCoreApplication.translate("TcpOutputPage", u"Headers", None))
#if QT_CONFIG(accessibility)
        self.plainTextEdit_tcpHeaders.setAccessibleName(QCoreApplication.translate("TcpOutputPage", u"WebSocket headers", None))
#endif // QT_CONFIG(accessibility)
#if QT_CONFIG(tooltip)
        self.plainTextEdit_tcpHeaders.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Extra headers for the WebSocket connection, one \"Name: value\" per line, for example an Authorization token", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(accessibility)
        self.pushButton_tcpStart.setAccessibleName(QCoreApplication.translate("TcpOutputPage", u"Start or stop the TCP / UDP output", None))
#endif // QT_CONFIG(accessibility)
#if QT_CONFIG(tooltip)
        self.pushButton_tcpStart.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Connect and send the readings, or stop", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpChannel.setText(QCoreApplication.translate("TcpOutputPage", u"Channel", None))
#if QT_CONFIG(tooltip)
        self.spinBox_tcpChannel.setToolTip(QCoreApplication.translate("TcpOutputPage", u"CasparCG's video channel ({channel} in the templates)", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpLayer.setText(QCoreApplication.translate("TcpOutputPage", u"Layer", None))
#if QT_CONFIG(tooltip)
        self.spinBox_tcpLayer.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The layer of the channel ({layer} in the templates)", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpCgLayer.setText(QCoreApplication.translate("TcpOutputPage", u"CG layer", None))
#if QT_CONFIG(tooltip)
        self.spinBox_tcpCgLayer.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The template's layer inside the CG producer ({cg_layer} in the templates)", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpTemplateName.setText(QCoreApplication.translate("TcpOutputPage", u"Template name", None))
#if QT_CONFIG(tooltip)
        self.lineEdit_tcpTemplateName.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The template's file name in CasparCG's templates folder, without .html ({template} in the templates)", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.pushButton_tcpLoad.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Send the \u201con connect\u201d message again (CG ADD: loads and plays the template)", None))
#endif // QT_CONFIG(tooltip)
        self.pushButton_tcpLoad.setText(QCoreApplication.translate("TcpOutputPage", u"Load", None))
#if QT_CONFIG(tooltip)
        self.pushButton_tcpPlay.setToolTip(QCoreApplication.translate("TcpOutputPage", u"CG PLAY: show the template", None))
#endif // QT_CONFIG(tooltip)
        self.pushButton_tcpPlay.setText(QCoreApplication.translate("TcpOutputPage", u"Play", None))
#if QT_CONFIG(tooltip)
        self.pushButton_tcpStop.setToolTip(QCoreApplication.translate("TcpOutputPage", u"CG STOP: hide the template (it animates out)", None))
#endif // QT_CONFIG(tooltip)
        self.pushButton_tcpStop.setText(QCoreApplication.translate("TcpOutputPage", u"Stop", None))
#if QT_CONFIG(tooltip)
        self.pushButton_tcpCopyTemplate.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Copy ScoreSight's scorebug template (scoresight_scorebug.html) into CasparCG's templates folder", None))
#endif // QT_CONFIG(tooltip)
        self.pushButton_tcpCopyTemplate.setText(QCoreApplication.translate("TcpOutputPage", u"Copy template\u2026", None))
        self.label_tcpMode.setText(QCoreApplication.translate("TcpOutputPage", u"Send", None))
#if QT_CONFIG(tooltip)
        self.comboBox_tcpMode.setToolTip(QCoreApplication.translate("TcpOutputPage", u"A message for each field whose value changed, or one message with every field", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpTemplate.setText(QCoreApplication.translate("TcpOutputPage", u"Template", None))
#if QT_CONFIG(tooltip)
        self.plainTextEdit_tcpTemplate.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Each line is sent as a line. Placeholders: {field}, {value}, {json}, {xml}, and {channel}, {layer}, {cg_layer}, {template} for CasparCG. Everything else is sent as it is.", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpInit.setText(QCoreApplication.translate("TcpOutputPage", u"On connect", None))
#if QT_CONFIG(tooltip)
        self.lineEdit_tcpInit.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Sent once after each connection, before the values (for example CasparCG's CG ADD). Empty: nothing.", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpPreview.setText(QCoreApplication.translate("TcpOutputPage", u"Preview", None))
#if QT_CONFIG(tooltip)
        self.plainTextEdit_tcpPreview.setToolTip(QCoreApplication.translate("TcpOutputPage", u"What is sent for the current values. \u240d is a carriage return, \u240a a line feed, \u2400 a NUL character.", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpLineEnding.setText(QCoreApplication.translate("TcpOutputPage", u"Line ending", None))
#if QT_CONFIG(tooltip)
        self.comboBox_tcpLineEnding.setToolTip(QCoreApplication.translate("TcpOutputPage", u"What ends each line of a message", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpEncoding.setText(QCoreApplication.translate("TcpOutputPage", u"Encoding", None))
#if QT_CONFIG(tooltip)
        self.comboBox_tcpEncoding.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The text encoding the receiving program expects. Characters it can't encode are sent as \u201c?\u201d.", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpEscape.setText(QCoreApplication.translate("TcpOutputPage", u"Escaping", None))
#if QT_CONFIG(tooltip)
        self.comboBox_tcpEscape.setToolTip(QCoreApplication.translate("TcpOutputPage", u"How the text of the placeholders is escaped, so that a value can't break the protocol", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpMinInterval.setText(QCoreApplication.translate("TcpOutputPage", u"At most every", None))
#if QT_CONFIG(tooltip)
        self.doubleSpinBox_tcpMinInterval.setToolTip(QCoreApplication.translate("TcpOutputPage", u"The shortest time between two sends: the values that change meanwhile go out together, the latest ones", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpRefresh.setText(QCoreApplication.translate("TcpOutputPage", u"Resend all every", None))
#if QT_CONFIG(tooltip)
        self.doubleSpinBox_tcpRefresh.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Send every value again this often, for a receiver that forgets them (a reloaded template, a restarted engine)", None))
#endif // QT_CONFIG(tooltip)
        self.label_tcpNames.setText(QCoreApplication.translate("TcpOutputPage", u"The name each field is sent as ({field}). An empty name leaves the field out.", None))
#if QT_CONFIG(tooltip)
        self.tableView_tcpMapping.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Each field and the name it is sent as. Edit the right column.", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.toolButton_tcpResetNames.setToolTip(QCoreApplication.translate("TcpOutputPage", u"Use the preset's names for every field again", None))
#endif // QT_CONFIG(tooltip)
        self.toolButton_tcpResetNames.setText(QCoreApplication.translate("TcpOutputPage", u"Reset names", None))
        pass
    # retranslateUi

