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

            // `model` is a context property, and inside a ComboBox it is also
            // that control's own item list - the combo came up empty until
            // the panel was captured under a name of its own.
            property var panel: model

            Text {
                Layout.fillWidth: true
                Layout.topMargin: 6
                text: "Which joint is which"
                color: palette.text
                font.pixelSize: 14
                font.bold: true
            }

            // Auto first, then every saved mapping with how much of the open
            // character it covers - a preset that only half fits is worse
            // than none, so the number is on the list rather than hidden.
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                spacing: 6

                ComboBox {
                    id: chooser
                    Layout.fillWidth: true
                    Layout.preferredHeight: 28
                    model: fields.panel.choices
                    currentIndex: fields.panel.choice
                    onActivated: fields.panel.choice = currentIndex
                }

                Button {
                    text: "Reload"
                    Layout.preferredHeight: 28
                    onClicked: fields.panel.refresh()
                }
            }

            Text {
                Layout.fillWidth: true
                text: fields.panel.note
                color: palette.text
                opacity: 0.75
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                spacing: 6

                Button {
                    text: "Match"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    onClicked: fields.panel.match()
                }

                Button {
                    text: "Register with Quick Rigging Tool"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    enabled: fields.panel.has_mapping
                    onClicked: fields.panel.register()
                }

                Button {
                    text: "Build rig"
                    Layout.preferredHeight: 30
                    enabled: fields.panel.has_mapping
                    onClicked: fields.panel.build()
                }
            }

            Text {
                Layout.fillWidth: true
                text: fields.panel.summary
                color: palette.text
                font.pixelSize: 12
            }

            // The mapping itself. Nothing is generated off it until it has
            // been read, because a wrong slot is worse than an empty one.
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 180
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
                        text: fields.panel.mapping_text
                        color: palette.text
                        font.family: "Consolas"
                        font.pixelSize: 11
                        wrapMode: TextArea.NoWrap
                        background: null
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                spacing: 6

                TextField {
                    id: nameField
                    Layout.fillWidth: true
                    Layout.preferredHeight: 28
                    placeholderText: "Name this mapping, e.g. Mixamo quadruped"
                    text: fields.panel.save_name
                    // The text sat high enough in the box to clip its own
                    // ascenders at this height; centre it explicitly.
                    verticalAlignment: TextInput.AlignVCenter
                    onTextEdited: fields.panel.save_name = text
                }

                Button {
                    text: "Save preset"
                    Layout.preferredHeight: 28
                    enabled: fields.panel.has_mapping && nameField.text.length > 0
                    onClicked: fields.panel.save()
                }

                Button {
                    text: "Delete"
                    Layout.preferredHeight: 28
                    enabled: fields.panel.can_delete
                    onClicked: fields.panel.remove()
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
