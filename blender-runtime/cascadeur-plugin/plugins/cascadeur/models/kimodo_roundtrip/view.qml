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
    // Every number the backend will accept is on the panel, and none of them
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
            // Weights are held as hundredths so a slider can carry them.
            property real display: 1.0
            property int decimals: 0

            signal edited(int newValue)

            spacing: 1
            Layout.fillWidth: true

            RowLayout {
                Layout.fillWidth: true
                // Held to the height of the text beside them. Left alone
                // these controls come up taller than the label and the row
                // reads as a button strip.
                Layout.preferredHeight: 26
                spacing: 6

                Text {
                    text: label
                    color: palette.text
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 26
                    font.pixelSize: 12
                    verticalAlignment: Text.AlignVCenter
                }

                Slider {
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

                // A plain field, not a SpinBox: the style's up/down buttons
                // take most of the width and leave no room for the number.
                // The height is the style's own padding plus the line - forced
                // shorter, the digits get cut off at the top.
                TextField {
                    Layout.preferredWidth: 68
                    Layout.preferredHeight: 26
                    implicitHeight: 26
                    topPadding: 3
                    bottomPadding: 3
                    font.pixelSize: 12
                    horizontalAlignment: TextInput.AlignRight
                    verticalAlignment: TextInput.AlignVCenter
                    selectByMouse: true
                    text: decimals > 0
                          ? (parent.parent.value / parent.parent.display).toFixed(decimals)
                          : parent.parent.value

                    onEditingFinished: {
                        var typed = parseFloat(text)
                        if (!isNaN(typed))
                            parent.parent.edited(Math.round(typed * parent.parent.display))
                        else
                            text = decimals > 0
                                   ? (parent.parent.value / parent.parent.display).toFixed(decimals)
                                   : parent.parent.value
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.leftMargin: 126
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
            implicitWidth: 820
            implicitHeight: 780

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

                    // `model` is a context property, not a property of root,
                    // so `root.model` does not resolve - and inside a ComboBox
                    // the bare name means that control's OWN model. Both
                    // combo boxes came up empty because of it. Captured here,
                    // where the plain name still means the panel.
                    property var panel: model

                    Item { Layout.preferredHeight: 2 }

                    // Which tab the motion lands on, and the two buttons that
                    // act on it. Kimodo generates onto a character already in
                    // the scene, so both belong here rather than in the row
                    // along the bottom - which they had made too long to fit,
                    // pushing Generate and Close off the edge of the window.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            Layout.fillWidth: true
                            text: fields.panel.target_text
                            color: palette.text
                            opacity: 0.75
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                        }

                        Dialogs.DialogButton {
                            text: "Open sample"
                            enabled: !fields.panel.busy
                            onClicked: fields.panel.open_sample()
                            ToolTip.visible: hovered
                            ToolTip.delay: 400
                            ToolTip.text: "Open the rigged character that "
                                          + "ships with the plugin, to "
                                          + "generate onto."
                        }

                        Dialogs.DialogButton {
                            text: "Remove frames"
                            enabled: !fields.panel.busy
                            onClicked: fields.panel.remove_frames()
                            ToolTip.visible: hovered
                            ToolTip.delay: 400
                            ToolTip.text: "Delete the frames Kimodo generated "
                                          + "on the selected character."
                        }
                    }

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
                            item.label = "Samples"
                            item.minimum = 1; item.maximum = 16
                            item.value = Qt.binding(() => model.samples)
                            item.hint = Qt.binding(() => model.samples_hint)
                            item.edited.connect((v) => model.samples = v)
                        }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "Sample index"
                            item.minimum = 0
                            item.maximum = Qt.binding(() => Math.max(0, model.samples - 1))
                            item.value = Qt.binding(() => model.sample_index)
                            item.hint = Qt.binding(() => model.sample_index_hint)
                            item.edited.connect((v) => model.sample_index = v)
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
                            item.label = "Denoising steps"
                            item.minimum = 1; item.maximum = 300; item.step = 5
                            item.value = Qt.binding(() => model.steps)
                            item.hint = Qt.binding(() => model.steps_hint)
                            item.edited.connect((v) => model.steps = v)
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Qt.rgba(1, 1, 1, 0.12)
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        CheckBox {
                            text: "Classifier free guidance"
                            checked: model.cfg
                            font.pixelSize: 12
                            onToggled: model.cfg = checked
                        }

                        Item { Layout.fillWidth: true }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "Text weight"
                            item.minimum = 0; item.maximum = 800; item.step = 5
                            item.display = 100.0; item.decimals = 2
                            item.value = Qt.binding(() => model.text_weight)
                            item.hint = Qt.binding(() => model.text_weight_hint)
                            item.edited.connect((v) => model.text_weight = v)
                        }
                    }

                    Loader {
                        Layout.fillWidth: true
                        sourceComponent: settingRow
                        onLoaded: {
                            item.label = "Constraint weight"
                            item.minimum = 0; item.maximum = 800; item.step = 5
                            item.display = 100.0; item.decimals = 2
                            item.value = Qt.binding(() => model.constraint_weight)
                            item.hint = Qt.binding(() => model.constraint_weight_hint)
                            item.edited.connect((v) => model.constraint_weight = v)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 26
                        spacing: 6

                        Text {
                            text: "Dataset"
                            color: palette.text
                            Layout.preferredWidth: 120
                            font.pixelSize: 12
                            verticalAlignment: Text.AlignVCenter
                        }

                        ComboBox {
                            Layout.preferredWidth: 150
                            Layout.preferredHeight: 26
                            font.pixelSize: 12
                            model: fields.panel.dataset_names
                            currentIndex: fields.panel.dataset
                            onActivated: fields.panel.dataset = currentIndex
                        }

                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 126
                        text: model.dataset_hint
                        color: palette.text
                        opacity: 0.55
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Qt.rgba(1, 1, 1, 0.12)
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 26
                        spacing: 6

                        Text {
                            text: "Text encoder"
                            color: palette.text
                            Layout.preferredWidth: 120
                            font.pixelSize: 12
                            verticalAlignment: Text.AlignVCenter
                        }

                        ComboBox {
                            Layout.preferredWidth: 190
                            Layout.preferredHeight: 26
                            font.pixelSize: 12
                            enabled: !fields.panel.backend_up
                            model: fields.panel.encoder_names
                            currentIndex: fields.panel.encoder
                            onActivated: fields.panel.encoder = currentIndex
                        }

                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 126
                        text: model.backend_up
                              ? "Chosen when Kimodo starts. Stop it to change."
                              : model.encoder_hint
                        color: palette.text
                        opacity: 0.55
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(96, backendLabel.implicitHeight + 14)
                        color: Qt.rgba(1, 1, 1, 0.04)
                        border.width: 1
                        border.color: Qt.rgba(1, 1, 1, 0.13)
                        radius: 3

                        Text {
                            id: backendLabel
                            anchors.fill: parent
                            anchors.margins: 7
                            text: model.backend_text
                            color: palette.text
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                            verticalAlignment: Text.AlignTop
                        }
                    }

                    Item { Layout.preferredHeight: 2 }
                }
            }

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
                    text: "Start Kimodo"
                    enabled: !model.busy && !model.backend_up
                    onClicked: model.start_backend()
                }

                Dialogs.DialogButton {
                    text: "Stop Kimodo"
                    enabled: !model.busy && model.backend_up
                    onClicked: model.stop_backend()
                }

                Dialogs.DialogButton {
                    text: "Refresh"
                    enabled: !model.busy
                    onClicked: model.refresh()
                }

                Item { Layout.fillWidth: true }

                Dialogs.DialogButton {
                    text: model.busy ? "Generating..." : "Generate"
                    enabled: !model.busy && model.can_generate
                    onClicked: model.generate()
                }

                Dialogs.DialogButton {
                    text: "Close"
                    onClicked: root.window.close()
                }
            }

            Item { Layout.preferredHeight: 2 }
        }
    }
}
