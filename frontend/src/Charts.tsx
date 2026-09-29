/**
 * Chart rendering, isolated from the shell.
 *
 * `recharts` and its d3 dependencies are the bulk of the bundle, and they are only needed
 * once an operator opens the forecast page. Loading them on demand keeps the login, network
 * and recommendation views small and fast on the demo's modest connections.
 */
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

const chartTooltip = { borderRadius: 12, border: '1px solid #e6e4de', boxShadow: '0 10px 30px rgb(20 20 18 / 10%)', fontSize: 12 };
const axisTick = { fill: '#8a877f', fontSize: 11 };

export interface ChartPoint {
  tick: number;
  actual?: number;
  forecast?: number;
  inventory?: number;
  band?: number[];
}

export function DemandChart({ data }: { data: ChartPoint[] }) {
  return <ResponsiveContainer width="100%" height={290}>
    <ComposedChart data={data} margin={{ top: 15, right: 25, bottom: 10, left: 5 }}>
      <CartesianGrid vertical={false} stroke="#ecebe6" />
      <XAxis dataKey="tick" tickLine={false} axisLine={false} tick={axisTick} />
      <YAxis tickLine={false} axisLine={false} tick={axisTick} />
      <Tooltip contentStyle={chartTooltip} />
      <Area dataKey="band" stroke="none" fill="#eef05b" fillOpacity={0.6} name="Demand band" />
      <Line dataKey="actual" stroke="#141412" strokeWidth={2} dot={false} name="Actual demand" />
      <Line dataKey="forecast" stroke="#8a877f" strokeWidth={1.6} strokeDasharray="5 4" dot={false} name="Forecast demand" />
    </ComposedChart>
  </ResponsiveContainer>;
}

export function InventoryChart({ data }: { data: ChartPoint[] }) {
  return <ResponsiveContainer width="100%" height={230}>
    <ComposedChart data={data}>
      <defs>
        <pattern id="inventory-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="7" height="7" fill="#f3f2ee" />
          <line x1="0" y1="0" x2="0" y2="7" stroke="#cfccc3" strokeWidth="1.5" />
        </pattern>
      </defs>
      <CartesianGrid vertical={false} stroke="#ecebe6" />
      <XAxis dataKey="tick" tickLine={false} axisLine={false} tick={axisTick} />
      <YAxis tickLine={false} axisLine={false} tick={axisTick} />
      <Tooltip contentStyle={chartTooltip} />
      <Area dataKey="inventory" stroke="#141412" strokeWidth={1.8} fill="url(#inventory-hatch)" fillOpacity={1} name="Fuel remaining (L)" />
    </ComposedChart>
  </ResponsiveContainer>;
}
