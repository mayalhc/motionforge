// Copyright (c) 2026 Chamiseul. All rights reserved.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import Dialogs as Dialogs

Dialogs.DialogBase {
    id: root

    contentItem: Loader {
        anchors {
            fill: parent
            leftMargin: 5
            rightMargin: 5
        }
        sourceComponent: model ? panelComponent : null
    }

    Component {
        id: panelComponent

        ColumnLayout {
            id: fields
            spacing: 8

            // `model` is a context property and also the name of a property
            // on several controls, so it is captured under its own name.
            property var panel: model

            Text {
                Layout.fillWidth: true
                Layout.topMargin: 6
                text: "MetaArKit - ARKit facial capture"
                color: palette.text
                font.pixelSize: 14
                font.bold: true
            }

            // Cascadeur does not tell the panel when the selection changes,
            // so the panel asks. Without this you could select the mesh
            // after choosing the file and nothing would react.
            Timer {
                interval: 700
                running: true
                repeat: true
                onTriggered: fields.panel.poll()
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Text {
                    text: "Selected mesh:"
                    color: palette.text
                    opacity: 0.75
                    font.pixelSize: 11
                }
                Text {
                    Layout.fillWidth: true
                    text: fields.panel.mesh_label
                    color: palette.text
                    opacity: fields.panel.mesh_label === "nothing selected" ? 1.0 : 0.75
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                spacing: 6

                Button {
                    text: "Choose CSV..."
                    Layout.preferredHeight: 28
                    onClicked: fields.panel.choose()
                }

                Text {
                    Layout.fillWidth: true
                    text: fields.panel.file_label
                    color: palette.text
                    font.pixelSize: 12
                    elide: Text.ElideMiddle
                    verticalAlignment: Text.AlignVCenter
                }
            }

            // Off by default: a CSV out of Blender is already the rig's exact
            // output, and filtering it was what made the face come in looking
            // wrong.
            CheckBox {
                text: "Smooth the curves (raw phone capture only)"
                checked: fields.panel.smooth
                Layout.preferredHeight: 24
                onToggled: fields.panel.smooth = checked
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 4
                columnSpacing: 8
                rowSpacing: 4

                Text {
                    text: "Window"
                    color: palette.text
                    font.pixelSize: 12
                    enabled: fields.panel.smooth
                    opacity: fields.panel.smooth ? 1.0 : 0.4
                }
                SpinBox {
                    from: 3; to: 201; stepSize: 2
                    value: fields.panel.window
                    Layout.preferredHeight: 26
                    enabled: fields.panel.smooth
                    onValueModified: fields.panel.window = value
                }

                Text {
                    text: "Poly order"
                    color: palette.text
                    font.pixelSize: 12
                    enabled: fields.panel.smooth
                    opacity: fields.panel.smooth ? 1.0 : 0.4
                }
                SpinBox {
                    from: 1; to: 9
                    value: fields.panel.order
                    Layout.preferredHeight: 26
                    enabled: fields.panel.smooth
                    onValueModified: fields.panel.order = value
                }

                Text {
                    text: "Start frame"
                    color: palette.text
                    font.pixelSize: 12
                }
                SpinBox {
                    from: 0; to: 100000
                    value: fields.panel.start
                    Layout.preferredHeight: 26
                    onValueModified: fields.panel.start = value
                }

                Text {
                    text: "Value scale"
                    color: palette.text
                    font.pixelSize: 12
                }
                SpinBox {
                    from: 1; to: 1000
                    value: Math.round(fields.panel.scale)
                    Layout.preferredHeight: 26
                    onValueModified: fields.panel.scale = value
                }
            }

            Text {
                Layout.fillWidth: true
                text: fields.panel.smoothing_hint
                color: fields.panel.valid ? palette.text : "#d05050"
                opacity: fields.panel.valid ? 0.75 : 1.0
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                spacing: 6

                Button {
                    text: "Check"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    enabled: !fields.panel.busy
                    onClicked: fields.panel.check()
                }

                Button {
                    text: "Import"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    enabled: fields.panel.ready && fields.panel.valid
                    onClicked: fields.panel.load()
                }
            }

            // A greyed button that does not say why is the worst of both.
            Text {
                Layout.fillWidth: true
                visible: fields.panel.blocked !== ""
                text: "Import needs: " + fields.panel.blocked
                color: palette.text
                opacity: 0.75
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            // Which column lands on which blendshape, before anything is
            // written - the old importer keyed first and told you after.
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 200
                color: palette.base
                border.color: palette.mid
                border.width: 1
                radius: 2

                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 4
                    clip: true

                    TextArea {
                        readOnly: true
                        text: fields.panel.preview
                        color: palette.text
                        font.family: "Consolas"
                        font.pixelSize: 11
                        wrapMode: TextArea.NoWrap
                        background: null
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.bottomMargin: 6
                text: fields.panel.status
                color: palette.text
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                Layout.topMargin: 2
                Layout.bottomMargin: 4
                text: "© 2026 Chamiseul. All rights reserved."
                color: palette.text
                opacity: 0.45
                font.pixelSize: 10
                horizontalAlignment: Text.AlignRight
            }
        }
    }
}
