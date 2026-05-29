import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { Label } from './label';
import { Toggle } from './toggle';

const meta = {
  title: 'UI/Toggle',
  component: Toggle,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
  args: { checked: false, disabled: false },
} satisfies Meta<typeof Toggle>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: (args) => {
    const [checked, setChecked] = useState(!!args.checked);
    return (
      <div className="flex items-center gap-2">
        <Toggle
          id="airplane"
          checked={checked}
          onCheckedChange={setChecked}
          disabled={args.disabled}
        />
        <Label htmlFor="airplane">Streaming preview</Label>
      </div>
    );
  },
};

export const Checked: Story = { args: { checked: true } };
export const Disabled: Story = { args: { disabled: true } };
export const DisabledChecked: Story = { args: { checked: true, disabled: true } };
