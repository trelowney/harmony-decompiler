const more = {};
// This is really a json file, but editing as js has better tools/commenting
/**
 * This file is a json-version of an Example.HarmonyConfig.bin file.  It is a representation of the binary data in a more human-readable format.  Things to note:
 * - All actions are just 24 bit values (not yet decoded).
 * - Objects are referred to by index (e.g. InfraredProtocolIndex, InfraredLanguageIndex, FragmentIndexes) rather than by name.
 * - Count of objects is inferred from the length of the array (e.g. InfraredDevices, Menus, StateVariables, InfraredProtocols, InfraredLanguages, BindingLists, ActionLists).
 */
/**
 * This is just my first guess of the format.  It is probably 60% complete, and what is here is probably 90% correct (e.g. 10% is WRONG, and will need more iterations of the spec)
 */
const FirstGuess = {
  $Metadata: {
    SourceFile: "Sample.HarmonyConfig.bin",
  },
  StateVariables: [
    {
      $Metadata: {
        ConfigOffset: 0x1234,
      },
      InitialValue: 0,
      States: [
        {
          Transitions: [
            {
              Options: 0,
              FromValue: 0,
              ToValue: 1,
              Action: 0x7d0102,
            },
            ...more,
          ],
        },
        ...more,
      ],
    },
    ...more,
  ],
  InfraredDevices: [
    {
      $Metadata: {
        ConfigOffset: 0x13566,
      },
      Commands: [
        {
          Type: 0,
          InfraredProtocolIndex: 0,
          Variants: [
            {
              Start: {
                InfraredLanguageIndex: 0,
                FragmentIndexes: [0, 3, 2, 0, 0, 4],
              },
              Repeat: { ...more },
              Finish: { ...more },
            },
            ...more,
          ],
        },
        ...more,
      ],
    },
    ...more,
  ],
  InfraredProtocols: [
    {
      CarrierPeriodNs: 48000000,
      CarrierPulseLengthNs: 24000000,
    },
    ...more,
  ],
  InfraredLanguages: [
    {
      Fragments: [
        {
          IntervalStream: [100, -100, 300, -300],
        },
        ...more,
      ],
    },
    ...more,
  ],
  Menus: [
    {
      $Metadata: {
        ConfigOffset: 0x1234,
      },
      Pages: [
        {
          BindingIndex: 0,
          RenderStream: [
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
                ImageIndex: 0,
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
  ],
  BindingLists: [
    {
      Bindings: [
        {
          Event: 0x83,
          Action: 0x7d0102,
        },
        {
          Options: 0,
          Event: 0x84,
          Action: 0x7d0105,
        },
        ...more,
      ],
    },
    ...more,
  ],
  ActionLists: [
    {
      ReversedActions: [0x123456, 0x789012, ...more], // Note that these actions are stored in reversed order - actual order would be 0x789012 followed by 0x123456.
    },
    ...more,
  ],
};
