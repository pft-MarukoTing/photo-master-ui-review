*** Settings ***
Library           AppiumLibrary
Resource          local_config.robot
# Test Teardown     Close Application

*** Variables ***
${REMOTE_URL}          http://127.0.0.1:4723
${BUNDLE_ID}           com.meitu.myxj
${SOURCE_PHOTO_START_OFFSET}         0
${REFERENCE_PHOTO_START_OFFSET}      50
${ITERATION_COUNT}                   50

*** Test Cases ***
Meitu Test Case
    Open Meitu Application
    FOR    ${i}    IN RANGE    ${ITERATION_COUNT}
        ${photo_offset}=    Evaluate    ${SOURCE_PHOTO_START_OFFSET} + ${i}
        ${custom_offset}=    Evaluate    ${REFERENCE_PHOTO_START_OFFSET} + ${i}
        Beautify Nail Art Photo    ${photo_offset}    ${custom_offset}
    END

*** Keywords ***
Beautify Nail Art Photo
    [Arguments]    ${photo_offset}    ${custom_offset}
    Click Page Element    accessibility_id=Beautify
    Scroll To Bottom Of Photo List    //XCUIElementTypeOther[@name="CosmesisPhotoContainer"]/XCUIElementTypeCollectionView
    Click Photo From Bottom By Grid    //XCUIElementTypeOther[@name="CosmesisPhotoContainer"]/XCUIElementTypeCollectionView    ${photo_offset}
    Click Page Element    xpath=//XCUIElementTypeButton[@name="Retouch"]
    Scroll To Retouch Feature    Nail Art
    Click Page Element    xpath=//XCUIElementTypeCollectionView/XCUIElementTypeCell[.//XCUIElementTypeStaticText[@name="Nail Art"]]
    Click Page Element    accessibility_id=Custom
    Scroll To Bottom Of Photo List    //XCUIElementTypeCollectionView
    Click Photo From Bottom By Grid    //XCUIElementTypeCollectionView    ${custom_offset}
    Confirm Nail Art Result
    Click Page Element    xpath=//XCUIElementTypeButton[@name="Save"]
    Click Page Element    xpath=//XCUIElementTypeWindow/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther[1]/XCUIElementTypeButton[1]
    Click Page Element    xpath=//XCUIElementTypeWindow/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther/XCUIElementTypeOther[1]/XCUIElementTypeOther[1]
    Click Page Element    accessibility_id=CosmesisPhotoBrowerBackButton

Open Meitu Application
    Open Application
    ...    ${REMOTE_URL}
    ...    platformName=iOS
    ...    automationName=XCUITest
    ...    deviceName=${DEVICE_NAME}
    ...    platformVersion=${PLATFORM_VERSION}
    ...    udid=${UDID}
    ...    bundleId=${BUNDLE_ID}
    ...    showXcodeLog=${True}
    ...    newCommandTimeout=3600

Click Page Element
    [Arguments]    ${element}    ${timeout}=20s
    Wait Until Element Is Visible    ${element}    timeout=${timeout}
    Click Element    ${element}

Scroll To Bottom Of Photo List
    [Arguments]    ${container_xpath}
    ${previous_y}=    Set Variable    ${NONE}
    FOR    ${i}    IN RANGE    30
        ${rect}=    Get Element Rect
        ...    xpath=${container_xpath}/XCUIElementTypeCell[last()]
        ${current_y}=    Set Variable    ${rect}[y]
        IF    $current_y == $previous_y
            BREAK
        END
        ${previous_y}=    Set Variable    ${current_y}
        Swipe    start_x=207    start_y=780    end_x=207    end_y=200    duration=150ms
        Sleep    0.5s
    END

Click Photo From Bottom By Grid
    [Arguments]    ${container_xpath}    ${offset}
    # Assumes a 3-column grid where only the last row may be partially filled.
    ${rect_last}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()]
    ${rect_1}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-1]
    ${rect_2}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-2]
    ${same_row_1}=    Evaluate    abs(${rect_last}[y] - ${rect_1}[y]) < 5
    ${same_row_2}=    Evaluate    abs(${rect_last}[y] - ${rect_2}[y]) < 5
    IF    ${same_row_2}
        ${col_last}=    Set Variable    ${2}
    ELSE IF    ${same_row_1}
        ${col_last}=    Set Variable    ${1}
    ELSE
        ${col_last}=    Set Variable    ${0}
    END

    # Reference the nearest fully-populated row above the last row to read column x-positions.
    ${offset_col2}=    Evaluate    ${col_last} + 1
    ${offset_col1}=    Evaluate    ${col_last} + 2
    ${offset_col0}=    Evaluate    ${col_last} + 3
    ${rect_col2}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-${offset_col2}]
    ${rect_col1}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-${offset_col1}]
    ${rect_col0}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-${offset_col0}]

    IF    ${offset} <= ${col_last}
        ${target_row}=    Set Variable    ${0}
        ${target_col}=    Evaluate    ${col_last} - ${offset}
    ELSE
        ${m}=    Evaluate    ${offset} - (${col_last} + 1)
        ${target_row}=    Evaluate    1 + ${m} // 3
        ${target_col}=    Evaluate    2 - (${m} % 3)
    END

    ${col_x}=    Create List    ${rect_col0}[x]    ${rect_col1}[x]    ${rect_col2}[x]
    ${target_x}=    Evaluate    ${col_x}[${target_col}] + ${rect_last}[width] // 2
    ${target_y}=    Evaluate    ${rect_last}[y] + ${rect_last}[height] // 2

    ${leftover_error}=    Scroll Up By Rows    ${container_xpath}    ${target_row}
    Sleep    1s

    ${target_y}=    Evaluate    ${target_y} + ${leftover_error}
    ${target_point}=    Create List    ${target_x}    ${target_y}
    Tap    ${target_point}

Scroll Up By Rows
    [Arguments]    ${container_xpath}    ${row_count}
    # Scrolls up by exactly ${row_count} rows using the real on-screen centers of
    # two currently-rendered cells as the swipe endpoints, instead of an estimated
    # pixel distance. Both endpoints are kept 1 row away from the rendered
    # top/bottom edge, since an edge cell can be partially clipped and its
    # reported center may fall outside the actually visible screen area.
    #
    # Swiping loses some of the requested distance to drag friction. Rather than
    # guessing a fixed compensation ratio, this keeps a handle on the dragged
    # cell itself, measures where it actually landed after each swipe, and folds
    # that shortfall into the next swipe's target so the error is corrected
    # instead of silently accumulating. Returns the final leftover pixel error
    # so the caller can adjust its own tap coordinate.
    ${remaining}=    Set Variable    ${row_count}
    ${pending_error}=    Set Variable    ${0}
    WHILE    ${remaining} > 0
        ${total_cells}=    Get Matching Xpath Count    ${container_xpath}/XCUIElementTypeCell
        ${num_rows}=    Evaluate    -(-${total_cells} // 3)
        ${max_step}=    Evaluate    max(1, ${num_rows} - 3)
        ${step}=    Evaluate    min(${remaining}, ${max_step})

        ${rect_last}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()]
        ${rect_1}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-1]
        ${rect_2}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-2]
        ${same_row_1}=    Evaluate    abs(${rect_last}[y] - ${rect_1}[y]) < 5
        ${same_row_2}=    Evaluate    abs(${rect_last}[y] - ${rect_2}[y]) < 5
        IF    ${same_row_2}
            ${col_last}=    Set Variable    ${2}
        ELSE IF    ${same_row_1}
            ${col_last}=    Set Variable    ${1}
        ELSE
            ${col_last}=    Set Variable    ${0}
        END

        ${idx_low}=    Evaluate    ${col_last} + 3
        ${idx_high}=    Evaluate    ${col_last} + 3 + 3 * ${step}
        ${dragged_cell}=    Get Webelement    xpath=${container_xpath}/XCUIElementTypeCell[last()-${idx_high}]
        ${rect_low}=    Get Element Rect    xpath=${container_xpath}/XCUIElementTypeCell[last()-${idx_low}]
        ${rect_high}=    Get Element Rect    ${dragged_cell}
        ${swipe_x}=    Evaluate    ${rect_low}[x] + ${rect_low}[width] // 2
        ${start_y}=    Evaluate    ${rect_high}[y] + ${rect_high}[height] // 2
        # Push the target by the outstanding error from the previous swipe, in
        # the same (downward) direction, to close the gap it left behind.
        ${target_y}=    Evaluate    ${rect_low}[y] + ${rect_low}[height] // 2 + ${pending_error}

        Swipe    start_x=${swipe_x}    start_y=${start_y}    end_x=${swipe_x}    end_y=${target_y}    duration=2000ms
        Sleep    0.5s

        ${rect_high_after}=    Get Element Rect    ${dragged_cell}
        ${actual_y}=    Evaluate    ${rect_high_after}[y] + ${rect_high_after}[height] // 2
        ${pending_error}=    Evaluate    ${target_y} - ${actual_y}

        ${remaining}=    Evaluate    ${remaining} - ${step}
    END
    RETURN    ${pending_error}

Scroll To Retouch Feature
    [Arguments]    ${feature_name}
    ${rect}=    Get Element Rect    xpath=//XCUIElementTypeCollectionView
    ${center_y}=    Evaluate    ${rect}[y] + ${rect}[height] // 2
    ${start_x}=    Evaluate    ${rect}[x] + ${rect}[width] * 3 // 4
    ${end_x}=    Evaluate    ${rect}[x] + ${rect}[width] // 4
    FOR    ${i}    IN RANGE    30
        ${visible}=    Run Keyword And Return Status
        ...    Element Should Be Visible
        ...    xpath=//XCUIElementTypeCollectionView/XCUIElementTypeCell[.//XCUIElementTypeStaticText[@name="${feature_name}"]]
        IF    ${visible}
            BREAK
        END
        Swipe    start_x=${start_x}    start_y=${center_y}    end_x=${end_x}    end_y=${center_y}    duration=300ms
        Sleep    0.5s
    END

Confirm Nail Art Result
    ${confirm_locator}=    Set Variable    xpath=//XCUIElementTypeWindow/XCUIElementTypeOther[2]/XCUIElementTypeOther/XCUIElementTypeOther[6]/XCUIElementTypeOther[2]/XCUIElementTypeButton[2]
    ${error_locator}=    Set Variable    xpath=//XCUIElementTypeStaticText[@name="Change photo"]
    ${error_shown}=    Set Variable    ${False}
    FOR    ${i}    IN RANGE    180
        ${error_shown}=    Run Keyword And Return Status    Element Should Be Visible    ${error_locator}
        IF    ${error_shown}    BREAK
        ${confirm_shown}=    Run Keyword And Return Status    Element Should Be Visible    ${confirm_locator}
        IF    ${confirm_shown}    BREAK
        Sleep    0.5s
    END
    IF    ${error_shown}
        Click Page Element    ${error_locator}
        Click Page Element    accessibility_id=MBCAlbumBackButton
    END
    Click Page Element    ${confirm_locator}
