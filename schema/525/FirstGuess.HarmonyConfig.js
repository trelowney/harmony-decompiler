const more = {};
// This is really a json file, but editing as js has better tools/commenting
/**
 * This file is a json-version Harmony remote configuration file.  It is a representation of the configuration in human-readable format.  Things to note (particularly compared to the raw version):
 * - Actions can now be lists
 * - Actions have symbolic properties
 * - Objects are referred to by name rather than by index
 * - Count of objects is inferred from the length of the array (e.g. InfraredDevices, Menus, StateVariables, InfraredProtocols, InfraredLanguages, BindingLists, ActionLists).
 */
/**
 * This is just my first guess of the format.  It is probably 70% complete, and what is here is probably 80% correct (e.g. 20% is WRONG, and will need more iterations of the spec)
 */
const FirstGuess = {
  StateVariables: [
    {
      InitialValue: 0,
      States: [
        {
          Transitions: [
            {
              FromValue: 0,
              ToValue: 1,
              Actions: [
                {
                  IrSend_IrDeviceId: "Television",
                  IrSend_IrCommandIndex: "Power",
                },
                ...more,
              ],
            },
            ...more,
          ],
        },
        ...more,
      ],
    },
    ...more,
  ],
  InfraredDevices: {
    Television: {
      Commands: {
        Play: {
          InfraredLanguageId: "Sony12Bit",
          Variants: [
            {
              Start: {
                FragmentId: "Header",
              },
              Repeat: {
                EncodingId: "Payload",
                Value: 0x12,
              },
              Finish: {
                FragmentId: "Trailer",
              },
            },
            ...more,
          ],
        },
        ...more,
      },
    },
    ...more,
  },
  InfraredLanguages: {
    Sony12Bit: {
      CarrierPeriodNs: 48000000,
      CarrierPulseLengthNs: 24000000,
      Fragments: {
        Header: {
          Intervals: [
            { Pulse: 100 },
            { Space: 100 },
            { Pulse: 300 },
            { Space: 300 },
          ],
        },
        Trailer: {
          Intervals: [{ Pulse: 100 }, { Space: 10000 }],
        },
        Payload: {
          Encoder: (value) => {
            return [{ Pulse: 100 }, { Space: 1000 + value * 100 }]; // Saves the file getting too big with expanded IR commands
          },
        },
        ...more,
      },
    },
    ...more,
  },
  Menus: {
    Home: {
      Pages: [
        {
          BindingList: [
            {
              Event: {
                Button_EventType: "Press",
                Button_Id: "Play",
              },
              Actions: [
                {
                  IrSend_IrDeviceId: "Television",
                  IrSend_IrCommandId: "Play",
                },
                ...more,
              ],
            },
          ],
          RenderList: [
            {
              Prepare: 0,
            },
            {
              DrawImage: {
                ScreenX: 0,
                ScreenY: 0,
                ImageX: 0,
                ImageY: 0,
                Width: 100,
                Height: 100,
                ImageId: "Blank",
              },
            },
            {
              Commit: true,
            },
            ...more,
          ],
        },
        ...more,
      ],
    },
  },
  Images: {
    Blank: {
      Width: 100,
      Height: 100,
      MonochromeBitmapData: [...more],
    },
    ...more,
  },
};
