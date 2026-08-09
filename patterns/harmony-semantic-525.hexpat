#pragma base_address 0x20000
import std.ptr;

using Address24 = u24;
using Address32 = u32;
using Event = u8;
struct UnknownSubsystem {
 //   char data[100];
};
struct Ptr24<T> {
    T *pData: Address24;
};
struct Ptr24Optional<T>{
    std::ptr::NullablePtr<T, Address24> pData;
};
struct Ptr32<T> {
    T *pData: Address32 [[inline]];
};
struct PtrSubsystem<T> {
    u8 unk1;
    std::ptr::NullablePtr<T, Address24> pData [[inline]];
};

struct Action {
    u8 byte0;
    u8 byte1;
    u8 byte2;
    if ( byte2 == 0x7E ){
        u16 menuSet_menuIndex = byte0 + (byte1 << 8) [[export]];
    } else if ( byte2 == 0x7D ) {
        u8 irSend_irDeviceIndex = byte1 [[export]];
        u8 irSend_irCommandIndex = byte0 [[export]];
    } else if ( byte2 == 0x7C ) {
        u8 irDelay_irDeviceIndex = byte1 [[export]];
        u8 irDelay_irDelay = byte0 [[export]];
    }
};

struct TailMarker {
    char magic[4];
};
fn isInformationRecord(auto offset){
    u8 type = std::mem::read_unsigned(offset, 1);
    if ( type == 0xA7 ){
        return true;
    }
    return false;
};
// ???GMH.  This is a blob of data that the remote firmware does not even parse.  I am pretty sure it is a binary
// blob that can be read by the client software (which will then decode the information records to try to work
// out useful things.
// 
// I am pretty sure that before upating a config, the client software will read this section, which will tell it
// the name of all state variables, including how many states each has.  It will then read the current values of
// those state variables.  Ifter the config is updated, it will examine the new config, and if the state variables
// are the same (including the same number of states), then it will set the value of those state variables to what
// they were prior to the config update.
//
// In opther words, if you are watching TV, and then update your remote, it should still think you are watching
// tv after the update.
struct InformationRecord {
    u8    type;                // Some kind of type (bit flags), followed by length of data
    u16 length;
    if ( type == 0xA7 ) {
    u16   unk1;          // ???GMH.  Some combination of state variable index (where the value is stored), initial value, bindings?
    u16   unk2;          // 
    char  name[length - 4];
    }
};
struct InformationSubsystem {
    u16         magicStart;        // 0xFEED
    u24 numBytes;
    InformationRecord  informationRecords[while(isInformationRecord($))];
    u16         magicEnd;   // 0xBEEF
};

struct Unknown5Record {
    u8 unk1;
    u8 count;
    u8 unk2;
    u24 unk3[count];
};
struct Unknown5RecordPtr {
    Unknown5Record *pRecord: Address24;
};
struct UnknownSubsystem5 {
    u8 count;
    Unknown5RecordPtr pRecords[count];    
};

struct Unknown6Record {
    u8 unknown[6];
};
struct Unknown6RecordPtr {
    Unknown6Record *pRecord: Address24;
};
struct UnknownSubsystem6 {
    u24 count;
   Unknown6RecordPtr pRecords[count]; 
//u24 bla[count];   
};

struct Unknown7Record {
    u8 unknown[6];
};
struct Unknown7RecordPtr {
    Unknown7Record *pRecord: Address24;
};
struct UnknownSubsystem7 {
    u16 count;
    Unknown7RecordPtr pRecords[count]; 
};

struct StartupSubsystem {
    u8 numStartupTypeActions;
    Action startupTypeActions[numStartupTypeActions];  // The client software can tell the firmware to 'start up' in different modes.  For example, after a firmware upgrade it can be told to go to the 'Upgrade Complete' menu
};

struct EventActionBinding {
    Event event;
    Action action;
};

struct EventActionBinding_Options {
    u8 options;
    Event event;
    Action action;
};

struct BindingList {
    u8 numBindingsOrSentinel;
    if ( numBindingsOrSentinel == 0 ) { 
        u8 numBindings;
        EventActionBinding_Options eventActionBindings[numBindings];    
    } else {
        EventActionBinding eventActionBindings[numBindingsOrSentinel];
    }
};

struct BindingSubsystem {
    u8 numBindingLists;
    Ptr24<BindingList> pBindingLists[numBindingLists];
};

// ???GMH.  Note that actions are stored in reversed order.  This allows the firmare to prepend actions to the beginning of the 'pending action' list.
struct ActionReversedList {
     u8 numActions;
     Action reversedActions[numActions];  
};
struct ActionSubsystem {
    u16 numActionLists;
    ActionReversedList reversedActionLists[numActionLists];
};
struct MenuItem {
    u24 unk1;
    BindingList bindingList;
};

struct MenuItemData<auto numMenuItems> {
    u8 data[numMenuItems];
};
// A helper struct that peeks ahead at the current offset '$'
// to check if the upcoming structure matches the null sentinel.
fn isNullRenderOperation(u64 offset) {
    // Return true if the critical fields (or all fields) are 0
    u8 type = std::mem::read_unsigned(offset, 1);
    if ( type == 0 ) {
        return true;
    }
    if ( type == 22 ) { // prepare??
        return false;
    }
    if ( type == 3 ) {
        return false;
    }
    if ( type == 23 ) { // commit?
        return false;
    }
    return true;
};
struct Image {
    u8 type;
    u16 width;
    u16 height;
    if ( type == 2){
        u8 data[width * height / 8];
    }
    
};
struct RenderImageOperation {
    u8 screenX;
    u8 screenY;
    u8 imageX;
    u8 imageY;
    u8 imageWidth;
    u8 imageHeight;
    Ptr24<Image> pImage;
};
struct RenderOperation {
    u8 type;
    if ( type == 22) {
        u8 unk1;
    } else if ( type == 3 ) {
        RenderImageOperation operation;
    }
};

struct RenderStream {
    RenderOperation items[while(!isNullRenderOperation($))];
    RenderOperation sentinel;
};

struct MenuPage {
    Ptr24<BindingList> pMenuPageBindingList [[inline]];
    Ptr24<RenderStream> pRenderStream [[inline]];
};
struct Menu {
    u8 options;
    Ptr24<BindingList> pMenuBindingList;
    u16 numPages;
    Ptr24<MenuPage> pMenuPages[numPages];
};
struct MenuSubsystem {
    u24 numMenus;
    Ptr24<Menu> pMenus[numMenus];
};

struct IrCommandInfo<auto type> {
    if ( type == 1){
        u24 carrierPeriodNs;
        u24 carrierPulseLengthNs;
    } else if ( type == 5){
        u24 carrierPeriodNs;
        u24 carrierPulseLengthNs;
    }
};
fn isNullIrCommandData(u64 offset) {
    // Return true if the critical fields (or all fields) are 0
    u16 value = std::mem::read_unsigned(offset, 2);
    if ( value == 0 ) {
        return true;
    }
    return false;
};
struct IrCommandDataValue {
    u16 value;
};

struct IrCommandDataStream {
    u16 numValues;
    IrCommandDataValue values[while(!isNullIrCommandData($))];
    IrCommandDataValue sentinel;
};

struct IrCommandSegments {
    u8 numSegments;
    Ptr24<IrCommandDataStream> pIrCommandDataString[numSegments];
};

struct IrCommandDataDefinition<auto type> {
    if ( type == 5 ) {
        Ptr24<IrCommandSegments> pIrCommandSegments;
        u16 numSegments;
        u8 segments[numSegments];
    }
};


struct IrCommandData<auto type> {
    Ptr24Optional<IrCommandDataDefinition<type>> pStart;
    Ptr24Optional<IrCommandDataDefinition<type>> pRepeat;
    Ptr24Optional<IrCommandDataDefinition<type>> pFinish;
};
struct IrCommand {
    u8 type;
    Ptr24<IrCommandInfo<type>> pIrCommandInfo;
    u8 numIrVariants; // Used for handling protocols that toggle a bit between button presses.  The remote stores the state of the toggle bit on a per-device basis, and switches between the two variants based on that bit.
    IrCommandData<type> irCommandVariants[numIrVariants];
};
struct IrDevice {
    u8 unk1;
    u16 numDeviceIrCommands;
    Ptr24<IrCommand> pIrCommands[numDeviceIrCommands] [[inline]];
};

struct IrSubsystem {
    u8 numIrDevices;
    Ptr24<IrDevice> pIrDevices[numIrDevices];
}; 

struct StateVariableTransition {
    u8 type;
    u16 fromValue;
    u16 toValue;
    Action action;
};
struct StateVariable {
    u16 initialValue;
    u16 maxValues;
    u24 numTransitions;
    StateVariableTransition stateVariableTransitions[numTransitions];
};
struct StateSubsystem {
    u16 numStateVariables;
    u16 unk1;
    u16 unk2; // Add to numStateVariables?
    u16 unk3;
    Ptr24<StateVariable> pStateVariables[numStateVariables];
};
struct RootHeader {
    char  magicStart[4];
    Ptr24<TailMarker> pTailMarker;
    u32 unk1;
    PtrSubsystem<InformationSubsystem> pInformationSubsystem;
    PtrSubsystem<UnknownSubsystem> pUnknown01;
    PtrSubsystem<UnknownSubsystem> pUnknown02;
    PtrSubsystem<UnknownSubsystem> pUnknown03;
    PtrSubsystem<UnknownSubsystem> pUnknown04;
    PtrSubsystem<IrSubsystem> pIrSubsystem;
  //  std::ptr::NullablePtr<UnknownSubsystem6, Address32> pUnknown06;
    PtrSubsystem<MenuSubsystem> pMenuSubsystem;
    PtrSubsystem<UnknownSubsystem7> pUnknown07;
    PtrSubsystem<StartupSubsystem> pStartupSubsystem;
    PtrSubsystem<BindingSubsystem> pBindingSubsystem;
    PtrSubsystem<ActionSubsystem> pActionSubsystem;
    PtrSubsystem<UnknownSubsystem> pUnknown11;
    PtrSubsystem<UnknownSubsystem> pUnknown12;
    PtrSubsystem<StateSubsystem> pStateSubsystem;
    PtrSubsystem<UnknownSubsystem> pUnknown13;
    PtrSubsystem<UnknownSubsystem> pUnknown14;
    PtrSubsystem<UnknownSubsystem> pUnknown15;
    PtrSubsystem<UnknownSubsystem> pUnknown16;
    PtrSubsystem<UnknownSubsystem> pUnknown17;
    PtrSubsystem<UnknownSubsystem> pUnknown18;
    char  magicEnd[4];
};


RootHeader rootHeader @ 0x20000;