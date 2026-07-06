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
    Scroll To Bottom Of Photo List
    Click Page Element    xpath=//XCUIElementTypeOther[@name="CosmesisPhotoContainer"]/XCUIElementTypeCollectionView/XCUIElementTypeCell[last()]/XCUIElementTypeOther/XCUIElementTypeImage

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
    ${previous_y}=    Set Variable    ${NONE}
    FOR    ${i}    IN RANGE    30
        ${rect}=    Get Element Rect
        ...    xpath=//XCUIElementTypeOther[@name="CosmesisPhotoContainer"]/XCUIElementTypeCollectionView/XCUIElementTypeCell[last()]
        ${current_y}=    Set Variable    ${rect}[y]
        IF    $current_y == $previous_y
            BREAK
        END
        ${previous_y}=    Set Variable    ${current_y}
        Swipe    start_x=207    start_y=750    end_x=207    end_y=250    duration=300ms
        Sleep    1.2s
    END
