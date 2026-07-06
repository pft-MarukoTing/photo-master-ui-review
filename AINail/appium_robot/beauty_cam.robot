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
