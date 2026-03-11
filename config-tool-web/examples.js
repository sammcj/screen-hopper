const examples = [
    {
        'description': '16:10 screen side to side with a 3:2 screen',
        'config':
        {
            "version": 4,
            "unmapped_passthrough": true,
            "partial_scroll_timeout": 1000000,
            "interval_override": 0,
            "constraint_mode": 2,
            "offscreen_sensitivity": 4000,
            "screens": [
                {
                    "x": 0,
                    "y": 0,
                    "w": 14400000,
                    "h": 9000000,
                    "sensitivity": 8000,
                    "output": 0
                },
                {
                    "x": 14400000,
                    "y": 0,
                    "w": 13500000,
                    "h": 9000000,
                    "sensitivity": 8000,
                    "output": 1
                }
            ],
            "mappings": [
            ]
        }
    },
    {
        'description': 'two 16:9 screens, one on top of the other',
        'config':
        {
            "version": 4,
            "unmapped_passthrough": true,
            "partial_scroll_timeout": 1000000,
            "interval_override": 0,
            "constraint_mode": 2,
            "offscreen_sensitivity": 4000,
            "screens": [
                {
                    "x": 0,
                    "y": 0,
                    "w": 16000000,
                    "h": 9000000,
                    "sensitivity": 4000,
                    "output": 0
                },
                {
                    "x": 0,
                    "y": 9000000,
                    "w": 16000000,
                    "h": 9000000,
                    "sensitivity": 4000,
                    "output": 1
                }
            ],
            "mappings": [
            ]
        }
    },
    {
        'description': '3 local monitors (L-shape) + remote on left',
        'config':
        {
            "version": 4,
            "unmapped_passthrough": true,
            "partial_scroll_timeout": 1000000,
            "interval_override": 0,
            "constraint_mode": 2,
            "offscreen_sensitivity": 4000,
            "screens": [
                {
                    "x": 0,
                    "y": 0,
                    "w": 8816168,
                    "h": 5698778,
                    "sensitivity": 4000,
                    "output": 1
                },
                {
                    "x": 12303277,
                    "y": 0,
                    "w": 6512890,
                    "h": 3663500,
                    "sensitivity": 4000,
                    "output": 0
                },
                {
                    "x": 8816168,
                    "y": 1745251,
                    "w": 3487109,
                    "h": 2254070,
                    "sensitivity": 4000,
                    "output": 0
                },
                {
                    "x": 14677769,
                    "y": 3663500,
                    "w": 3256445,
                    "h": 2035278,
                    "sensitivity": 4000,
                    "output": 0
                }
            ],
            "mappings": [
                {
                    "source_usage": "0x00070039",
                    "target_usage": "0xfff20001",
                    "layer": 0,
                    "sticky": false,
                    "scaling": 1000
                }
            ]
        }
    },
];

export default examples;
