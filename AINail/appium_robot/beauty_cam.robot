*** Settings ***
Library           AppiumLibrary
Resource          local_config.robot
# Test Teardown     Close Application

*** Variables ***
${REMOTE_URL}          http://127.0.0.1:4723
${BUNDLE_ID}           com.meitu.myxj

*** Test Cases ***
Meitu Test Case
    Open Meitu Application
    Click Page Element    accessibility_id=Beautify
    Scroll To Bottom Of Photo List    //XCUIElementTypeOther[@name="CosmesisPhotoContainer"]/XCUIElementTypeCollectionView
    Click Page Element    xpath=//XCUIElementTypeOther[@name="CosmesisPhotoContainer"]/XCUIElementTypeCollectionView/XCUIElementTypeCell[last()]/XCUIElementTypeOther/XCUIElementTypeImage
    Click Page Element    xpath=//XCUIElementTypeButton[@name="Retouch"]
    Scroll To Retouch Feature    Nail Art
    Click Page Element    xpath=//XCUIElementTypeCollectionView/XCUIElementTypeCell[.//XCUIElementTypeStaticText[@name="Nail Art"]]
    Click Page Element    accessibility_id=Custom
    Scroll To Bottom Of Photo List    //XCUIElementTypeCollectionView

*** Keywords ***
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
    [Arguments]    ${element}
    Wait Until Element Is Visible    ${element}    timeout=20s
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
        Swipe    start_x=207    start_y=750    end_x=207    end_y=250    duration=300ms
        Sleep    1.2s
    END

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
