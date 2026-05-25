import type { Meta, StoryObj } from '@storybook/react-vite';
import { Slider } from './slider';

const meta = {
  title: 'UI/Slider',
  component: Slider,
  tags: ['autodocs'],
  args: { defaultValue: [40], min: 0, max: 100, step: 1 },
  decorators: [
    (Story) => (
      <div className="w-[320px]">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof Slider>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Stepped: Story = { args: { defaultValue: [25], step: 25 } };
export const Range: Story = { args: { defaultValue: [25, 75] } };
export const Disabled: Story = { args: { defaultValue: [40], disabled: true } };
