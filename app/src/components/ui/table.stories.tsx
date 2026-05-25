import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './table';

const meta = {
  title: 'UI/Table',
  component: Table,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Table>;

export default meta;

type Story = StoryObj<typeof meta>;

const rows = [
  { id: 'narrator', engine: 'Kokoro', duration: '12:03', updated: '2 days ago' },
  { id: 'documentary', engine: 'Supertonic-3', duration: '8:41', updated: '5 days ago' },
  { id: 'whisper', engine: 'Kyutai Pocket', duration: '4:22', updated: '2 weeks ago' },
];

export const Default: Story = {
  render: () => (
    <Table className="w-[520px]">
      <TableCaption>Voice profiles available in this workspace.</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Profile</TableHead>
          <TableHead>Engine</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Updated</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id}>
            <TableCell className="font-medium capitalize">{row.id}</TableCell>
            <TableCell>{row.engine}</TableCell>
            <TableCell>{row.duration}</TableCell>
            <TableCell className="text-muted-foreground">{row.updated}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ),
};
