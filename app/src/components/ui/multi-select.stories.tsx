import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { MultiSelect, type MultiSelectOption } from './multi-select';

const options: MultiSelectOption[] = [
  { value: 'en', label: 'English' },
  { value: 'fr', label: 'French' },
  { value: 'es', label: 'Spanish' },
  { value: 'de', label: 'German' },
  { value: 'ja', label: 'Japanese' },
];

const ControlledMultiSelect = ({ initial = [] }: { initial?: string[] }) => {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <div className="w-[280px]">
      <MultiSelect
        options={options}
        value={value}
        onChange={setValue}
        placeholder="Pick languages"
      />
    </div>
  );
};

const meta = {
  title: 'UI/MultiSelect',
  component: ControlledMultiSelect,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof ControlledMultiSelect>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Preselected: Story = { args: { initial: ['en', 'fr'] } };
