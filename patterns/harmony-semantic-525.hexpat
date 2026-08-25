#pragma base_address 0x20000
import std.ptr;
import type.base;
import std.core;

using Address24 = u24;
using Address32 = u32;
using Event = u8;
struct UnknownSubsystem {
    char data[100];
};
struct Ptr24<T> {
    T *pData: Address24 [[inline]];
};
struct Ptr24Optional<T>{
    std::ptr::NullablePtr<T, Address24> pData [[inline]];
};
struct Ptr24Raw<T>{
     type::Hex<u24> rawPointer;
};
struct Ptr32<T> {
    T *pData: Address32 [[inline]];
};
struct Ptr32Raw<T>{
     type::Hex<u32> rawPointer;
};
struct PtrSubsystem<T> {
    u8 unk1;
    std::ptr::NullablePtr<T, Address24> pData [[inline]];
};
struct PtrSubsystemRaw<T> {
    u8 unk1;
    type::Hex<u24> rawPointer;
};
using ActionDecode;

#define MINIMAL
#ifdef MINIMAL
    #define Ptr24Minimal Ptr24Raw
    #define PtrSubsystemMinimal PtrSubsystemRaw
    #define Action type::Hex<u24>
#endif
#ifndef MINIMAL
    #define Ptr24Minimal Ptr24
    #define PtrSubsystemMinimal PtrSubsystem
    #define Action ActionDecode
#endif

fn isSentinelU8(auto offset) {
    // Return true if the critical field is zero
    u8 sentinel = std::mem::read_unsigned(offset, 1);
    if ( sentinel == 0 ) {
        return true;
    }
    return false;
};
fn isSentinelU16(auto offset) {
    // Return true if the critical field is zero
    u16 sentinel = std::mem::read_unsigned(offset, 2);
    if ( sentinel == 0 ) {
        return true;
    }
    return false;
};

/*
Actions
*/
// Note that bitfelds are def
bitfield State_SetValue_Action {
    opcode: 1;
    stateVariableIndex: 7;
    stateVariableValue: 16;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];
bitfield Menu_SetMenu_Action {
    opcode: 8;
    menuIndex: 16;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];
bitfield Action_QueueActionList_Action {
    opcode: 8;
    actionListIndex: 16;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];
bitfield Ir_QueueCommand_Action {
    opcode: 8;
    irDeviceIndex: 8;
    irCommandIndex: 8;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];
bitfield Ir_QueueDelay_Action {
    opcode: 8;
    irDeviceIndex: 8;
    irDelayValue: 8;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];
bitfield Sound_PlaySound_Action {
    opcode: 8;
    irDeviceIndex: 8;
    irDelayValue: 8;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];
bitfield Render_RenderStream_Action {
    opcode: 8;
    renderStreamIndex: 16;
} [[bitfield_order(std::core::BitfieldOrder::MostToLeastSignificant, 24)]];

struct ActionDecode {
    type::Hex<u24> data = std::mem::read_unsigned($, 3);
    if ( (data & 0x800000) == 0x800000){
        State_SetValue_Action action [[inline]];
    } else {
        type::Hex<u8> byte2 = data >> 16;
        match ( byte2 ) {
            (0x7F): {
                Action_QueueActionList_Action action [[inline]];
            }
            (0x7E): {
                Menu_SetMenu_Action action [[inline]];
            }
            (0x7D): {
                Ir_QueueCommand_Action action [[inline]];
            }
            (0x7C): {
                Ir_QueueDelay_Action action [[inline]];
            }
            (0x75): {
                Sound_PlaySound_Action action [[inline]];
            }
            (0x73): {
                Render_RenderStream_Action action [[inline]];
            }
            (_): {
                type::Hex<u24> rawAction[[export]];
            }
        }
    }
};
struct Action24 {
    type::Hex<u24> data;
};
/*
Actions
*/


/*
Render
*/


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
    Ptr24Minimal<Image> pImage;
};
struct RenderImageSubRectOperation {
    u8 screenX;
    u8 screenY;
    u8 imageSubRectX;
    u8 imageSubRectY;
    u8 imageSubRectWidth;
    u8 imageSubRectHeight;
    Ptr24Minimal<Image> pImage;
};

struct GlyphIndex {
    u8 glyphIndex;
};

struct GlyphIndexStream {
    GlyphIndex items[while(!isSentinelU8($))];
    GlyphIndex sentinel;
};

using RenderStream;
struct RenderTextPointerOperation {
    u8 screenX;
    u8 screenY;
    Ptr24<GlyphIndexStream> pText [[inline]];
};
struct RenderTextInlineOperation {
    u8 screenX;
    u8 screenY;
    GlyphIndexStream text [[inline]];
};

struct RenderStateVariableJumpOperation {
    u8 stateVariableIndex;
    u8 stateVariableValue;
};
struct RenderSetDisplayRowOperation {
    u8 displayRowIndex;
};
struct RenderTransferDisplayRowOperation {
};
struct RenderStateVariableMatch<T> {
    T value;
    Ptr24<RenderStream> pRenderStream;
};
struct RenderStateVariableRange<T> {
    T valueMin;
    T valueMax;
    Ptr24<RenderStream> pRenderStream;
};
struct RenderStateVariableOperation<T> {
    u8 stateVariableIndex;
    T stateVariableMatchCount;
    RenderStateVariableMatch<T> renderMatches;
    T stateVariableRangeCount;
    RenderStateVariableRange<T> renderRanges;
};
struct RenderActionOperation {
    Action action;
};
struct RenderRenderStreamOperation {
    Ptr24<RenderStream> pRenderStream;
};
struct RenderSetFontOperation {
    u8 fontIndex;
};
struct RenderOperation {
    u8 opcode;
    match ( opcode ) {
        (0x00): {}
        (0x01): {
            u8 unk1[6];
        }
        (0x02): {
            RenderImageOperation operation [[inline]];
        }
        (0x03): {
            RenderImageSubRectOperation operation [[inline]];
        }
        (0x04): {
            RenderTextPointerOperation operation [[inline]];
        }
        (0x05): {
            RenderTextInlineOperation operation [[inline]];
        }
        (0x10): {
            RenderSetFontOperation operation [[inline]];
        }
        (0x11): {
            RenderActionOperation operation [[inline]];
        }
        (0x12): {
            RenderStateVariableOperation<u8> operation [[inline]];
        }
        (0x13): {
            RenderStateVariableOperation<u16> operation [[inline]];
        }
        (0x14): {
            RenderRenderStreamOperation operation [[inline]];
        }
        (0x15): {
            u8 unk1[4];
        }
        (0x16): {
            RenderSetDisplayRowOperation operation;
        }
        (0x17): {
            RenderTransferDisplayRowOperation operation [[inline]];
        }

    }
};

// A helper struct that peeks ahead at the current offset '$'
// to check if the upcoming structure matches the null sentinel.
fn isSentinelRenderOperation(u64 offset) {
    // Return true if the critical fields (or all fields) are 0
    u8 opcode = std::mem::read_unsigned(offset, 1);
    if ( opcode == 0 ) {
        return true;
    }
    if ( opcode >= 1 && opcode <= 5 ) {
        return false;
    }
    if ( opcode >= 0x10 && opcode <= 0x17 ){
        return false;
    }
    // Unknown, stop now but needs investigation
    return true;
};
struct RenderStream {
    RenderOperation items[while(!isSentinelRenderOperation($))];
    RenderOperation sentinel;
};
/*
Render
*/

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


struct StartupSubsystem {
    u8 numStartupTypeActions;
    Action startupTypeActions[numStartupTypeActions];  // The client software can tell the firmware to 'start up' in different modes.  For example, after a firmware upgrade it can be told to go to the 'Upgrade Complete' menu
};

struct GlyphOperation {
    u8 unk1;
};
struct GlyphStream {
    GlyphOperation items[while(!isSentinelU8($))];
    GlyphOperation sentinel;
};
struct Glyph {
    u8 width;
    GlyphStream operations;
};

struct Font {
    u8 unk1;
    u8 minValue;
    u8 numGlyphs;
    Ptr24Minimal<Glyph> pGlyphs[numGlyphs];
};

struct FontSubsystem {
    u8 numFonts;
    u8 unk1;
    Ptr24<Font> pFonts[numFonts] [[inline]];
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

struct IrCommandDataValue {
    u16 value;
};

struct IrCommandDataStream {
    u16 numValues;
    IrCommandDataValue values[while(!isSentinelU16($))];
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

struct RenderSubsystem {
    u16 numRenderStreams;
    Ptr24<RenderStream> renderStreams[numRenderStreams];
};
struct Subsystem11Data {
    u32 unk1;
    Action action;
};
struct UnknownSubsystem11 {
    u8 numChildren;
    Ptr24<Subsystem11Data> pChildren[numChildren];
};

struct Subsystem13Data1 {
    u16 unk1;
    Ptr24<RenderStream> pRenderStream;
};
struct Subsystem13Data2 {
    u16 unk1;
    u16 unk2;
    Ptr24<RenderStream> pRenderStream;
};
struct Subsystem13Data {
    u8 unk1;
    u8 numRecords1;
    Subsystem13Data1 records1[numRecords1];
    u8 numRecords2;
    Subsystem13Data2 records2[numRecords2];
};
struct UnknownSubsystem13 {
    u8 numChildren;
    Ptr24<Subsystem13Data> pChildren[numChildren];
};
struct UnknownSubsystem1 {
    u8 unk1[7];
};
struct UnknownSubsystem2 { // Flash storage?
     u16 numMaxRecords; // *8 byte records = finish - start
     type::Hex<u24> pWriteableFlashStart;
     type::Hex<u24> pWriteableFlashFinish;
};

struct UnknownSubsystem3 {
    u8 unk1[14];
};
struct SystemMessage {
    u8 code;
    u24 menuIndex;
};
struct SystemMessageSubsystem {
    u24 defaultMenuIndex;
    u16 numSystemMessages;
    SystemMessage systemMessages[numSystemMessages];
};

struct Parameters {
    u8 numValues;
    u16 values[numValues];
};

struct ParameterSubsystem {
    u8 numParameters;
    Ptr24<Parameters> pParameters[numParameters];
};
struct RootHeader {
    char  magicStart[4];
    Ptr24<TailMarker> pTailMarker;
    u32 unk1;
    PtrSubsystem<InformationSubsystem> pInformationSubsystem;
    PtrSubsystem<UnknownSubsystem1> pUnknown01;
    PtrSubsystem<UnknownSubsystem2> pUnknown02;
    PtrSubsystem<UnknownSubsystem3> pUnknown03;
    PtrSubsystem<SystemMessageSubsystem> pSystemMessageSubsystem;
    PtrSubsystemMinimal<IrSubsystem> pIrSubsystem;
    PtrSubsystem<MenuSubsystem> pMenuSubsystem;
    PtrSubsystem<FontSubsystem> pFontSubsystem;
    PtrSubsystem<StartupSubsystem> pStartupSubsystem;
    PtrSubsystem<BindingSubsystem> pBindingSubsystem;
    PtrSubsystem<ActionSubsystem> pActionSubsystem;
    PtrSubsystem<RenderSubsystem> pRenderSubsystem;
    PtrSubsystem<UnknownSubsystem11> pUnknown11;
    PtrSubsystem<StateSubsystem> pStateSubsystem;
    PtrSubsystem<UnknownSubsystem13> pUnknown13;
    PtrSubsystem<ParameterSubsystem> pParameterSubsystem;
    PtrSubsystem<UnknownSubsystem> pUnknown15;
    PtrSubsystem<UnknownSubsystem> pUnknown16;
    PtrSubsystem<UnknownSubsystem> pUnknown17; // null
    PtrSubsystem<UnknownSubsystem> pUnknown18; // null
    char  magicEnd[4];
};


RootHeader rootHeader @ 0x20000;