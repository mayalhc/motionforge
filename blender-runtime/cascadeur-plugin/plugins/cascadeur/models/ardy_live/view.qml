// Copyright (c) 2026 Chamiseul. All rights reserved.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import Dialogs as Dialogs
import NControls as NControls

import "../CommonQmlItems"

Dialogs.DialogBase {
    id: root

    contentItem: Loader {
        anchors {
            fill: parent
            leftMargin: 5
            rightMargin: 5
        }

        sourceComponent: model ? dialogComponent : null
    }

    // A labelled slider with its value and a line of explanation under it.
    // Every number the service will accept is on the panel, and none of them
    // are worth guessing at, so each one says what it costs.
    Component {
        id: settingRow

        ColumnLayout {
            property string label
            property string hint
            property int value
            property int minimum
            property int maximum
            property int step: 1

            signal edited(int newValue)

            spacing: 1
            Layout.fillWidth: true

            RowLayout {
                Layout.fillWidth: true
                // Held to the height of the text beside them. Left to their
                // own devices these controls come up far taller than the
                // label and the row reads as a button strip.
                Layout.preferredHeight: 26
                spacing: 6

                Text {
                    text: label
                    color: palette.text
                    Layout.preferredWidth: 112
                    Layout.preferredHeight: 26
                    font.pixelSize: 12
                    verticalAlignment: Text.AlignVCenter
                }

                Slider {
                    id: bar
                    Layout.fillWidth: true
                    Layout.preferredHeight: 20
                    implicitHeight: 20
                    from: minimum
                    to: maximum
                    stepSize: step
                    snapMode: Slider.SnapAlways
                    value: parent.parent.value
                    onMoved: parent.parent.edited(Math.round(value))
                }

                // A plain field, not a SpinBox. The style's up/down buttons
                // took most of the width and left no room for the number.
                //
                // The height is the style's own padding plus the line, not a
                // round number: forcing it shorter than the style expects
                // pushed the digits up until their tops were cut off.
                TextField {
                    id: box
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 26
                    implicitHeight: 26
                    topPadding: 3
                    bottomPadding: 3
                    font.pixelSize: 12
                    horizontalAlignment: TextInput.AlignRight
                    verticalAlignment: TextInput.AlignVCenter
                    selectByMouse: true
                    text: parent.parent.value
                    validator: IntValidator { bottom: minimum; top: maximum }

                    onEditingFinished: {
                        var typed = parseInt(text)
                        if (!isNaN(typed))
                            parent.parent.edited(typed)
                        else
                            text = parent.parent.value
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.leftMargin: 118
                text: hint
                color: palette.text
                opacity: 0.55
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
        }
    }

    Component {
        id: dialogComponent

        // Everything that can grow is in the scroll area; the buttons and the
        // progress bar sit below it, where nothing can push them off.
        ColumnLayout {
            spacing: 6
            implicitWidth: 680
            implicitHeight: 760

            Flickable {
                id: scroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 140
                clip: true
                contentWidth: width
                contentHeight: fields.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar {
                    policy: scroll.contentHeight > scroll.height
                            ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded
                }

                ColumnLayout {
                    id: fields
                    width: scroll.width - 14
                    spacing: 8

                    Item { Layout.preferredHeight: 2 }

                    ItemStr {
                        Layout.fillWidth: true
                        name: "Prompt"
                        value: model.prompt
                        onEditFinished: (newValue) => { model.prompt = newValue }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "Frames"
                            item.minimum = 20; item.maximum = 600; item.step = 20
                            item.value = Qt.binding(() => model.frames)
                            item.hint = Qt.binding(() => model.frames_hint)
                            item.edited.connect((v) => model.frames = v)
                        }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "Diffusion steps"
                            item.minimum = 1; item.maximum = 60
                            item.value = Qt.binding(() => model.steps)
                            item.hint = Qt.binding(() => model.steps_hint)
                            item.edited.connect((v) => model.steps = v)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        Loader {
                            Layout.fillWidth: true
                            sourceComponent: settingRow
                            onLoaded: {
                                item.label = "Seed"
                                item.minimum = -1; item.maximum = 100000
                                item.value = Qt.binding(() => model.seed)
                                item.hint = Qt.binding(() => model.seed_hint)
                                item.edited.connect((v) => model.seed = v)
                            }
                        }

                        Dialogs.DialogButton {
                            text: "Random"
                            onClicked: model.randomise_seed()
                        }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "History frames"
                            item.minimum = 0; item.maximum = 120; item.step = 4
                            item.value = Qt.binding(() => model.history)
                            item.hint = Qt.binding(() => model.history_hint)
                            item.edited.connect((v) => model.history = v)
                        }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "Take crossover"
                            item.minimum = 0; item.maximum = 40
                            item.value = Qt.binding(() => model.transition)
                            item.hint = Qt.binding(() => model.transition_hint)
                            item.edited.connect((v) => model.transition = v)
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Qt.rgba(1, 1, 1, 0.12)
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        Text {
                            text: "Take list"
                            color: palette.text
                            opacity: 0.8
                        }

                        Item { Layout.fillWidth: true }

                        Dialogs.DialogButton {
                            text: "Add take"
                            enabled: !model.busy
                            onClicked: model.add_take()
                        }

                        Dialogs.DialogButton {
                            text: "Remove last"
                            enabled: !model.busy
                            onClicked: model.remove_last_take()
                        }

                        Dialogs.DialogButton {
                            text: "Clear"
                            enabled: !model.busy
                            onClicked: model.clear_takes()
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(84, takesLabel.implicitHeight + 14)
                        color: Qt.rgba(1, 1, 1, 0.04)
                        border.width: 1
                        border.color: Qt.rgba(1, 1, 1, 0.13)
                        radius: 3

                        Text {
                            id: takesLabel
                            anchors.fill: parent
                            anchors.margins: 7
                            text: model.takes_text
                            color: palette.text
                            wrapMode: Text.WordWrap
                            verticalAlignment: Text.AlignTop
                        }
                    }

                    Item { Layout.preferredHeight: 2 }
                }
            }

            // Moves while a take is being made, because generation runs on a
            // worker thread - doing it in the click handler froze the window
            // and the bar with it.
            ProgressBar {
                Layout.fillWidth: true
                Layout.preferredHeight: 6
                from: 0
                to: 1
                value: model.progress
                visible: model.busy || model.progress > 0
            }

            Text {
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                text: model.status
                color: palette.text
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }

            RowLayout {
                Layout.fillWidth: true

                Dialogs.DialogButton {
                    text: "Start server"
                    enabled: !model.busy && !model.service_up
                    onClicked: model.start_server()
                }

                Dialogs.DialogButton {
                    text: "Stop server"
                    enabled: !model.busy && model.service_up
                    onClicked: model.stop_server()
                }

                Dialogs.DialogButton {
                    text: "Refresh"
                    enabled: !model.busy
                    onClicked: model.refresh()
                }

                Item { Layout.fillWidth: true }

                ComboBox {
                    id: engineBox
                    model: ["ARDY", "Kimodo", "MotionBricks"]
                    currentIndex: root.model ? root.model.engine_index : 0
                    enabled: !root.model.busy && !root.model.live
                    font.pixelSize: 12
                    ToolTip.visible: hovered
                    ToolTip.text: root.model.engine_hint
                    ToolTip.delay: 400
                    onActivated: root.model.engine_index = index
                }

                CheckBox {
                    id: newSceneBox
                    text: "New tab"
                    checked: model.new_scene
                    enabled: !model.busy && !model.live
                    font.pixelSize: 12
                    ToolTip.visible: hovered
                    ToolTip.text: model.new_scene_hint
                    ToolTip.delay: 400
                    onToggled: model.new_scene = checked
                }


                // Keeps generating into the open scene until stopped. The
                // prompt is fixed for the run; the take list is not used.
                Dialogs.DialogButton {
                    text: model.live ? "Stop live" : "Live"
                    enabled: !model.busy
                    onClicked: model.toggle_live()
                    ToolTip.visible: hovered
                    ToolTip.delay: 400
                    ToolTip.text: model.live
                        ? "Stop generating."
                        : "Generate without stopping, appending to the take as "
                          + "it plays. Measured 4x faster than playback at 8 steps."
                }

                Dialogs.DialogButton {
                    text: model.busy ? "Generating..." : "Generate"
                    enabled: !model.busy
                    onClicked: model.generate()
                }

                Dialogs.DialogButton {
                    text: "Close"
                    onClicked: root.window.close()
                }
            }

            Item { Layout.preferredHeight: 2 }

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
