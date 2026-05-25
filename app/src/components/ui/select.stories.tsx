import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from './select';

const meta = {
  title: 'UI/Select',
  component: Select,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Select>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="w-[260px]">
      <Select>
        <SelectTrigger>
          <SelectValue placeholder="Pick a voice" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>Profiles</SelectLabel>
            <SelectItem value="narrator">Narrator</SelectItem>
            <SelectItem value="documentary">Documentary</SelectItem>
            <SelectItem value="whisper">Whisper</SelectItem>
          </SelectGroup>
          <SelectGroup>
            <SelectLabel>Presets</SelectLabel>
            <SelectItem value="kokoro">Kokoro</SelectItem>
            <SelectItem value="supertonic">Supertonic-3</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  ),
};
